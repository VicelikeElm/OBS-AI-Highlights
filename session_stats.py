# -*- coding: utf-8 -*-
"""Per-session detection statistics and a lightweight session registry.

A "session" corresponds to one continuous run_live_sermon() call - one
OBS activity period, from when live capture starts until the service
ends. This is a metadata/stats layer only - it does NOT move or
reorganize the actual Raw/Verified/Review/Ready clip files, which stay
in their existing flat, already-proven folder structure. Sessions are
for browsing history and tuning presets against real detection data
(how many thoughts were analyzed, how many were possible/saved, which
phrases actually drove scores, average score) rather than guessing.

One JSON file per session under <output folder>/Sessions/<session_id>.json,
written atomically on every update so a mid-session crash loses at most
the last single event, not the whole session's history.
"""

import json
from datetime import datetime
from pathlib import Path

import config as app_config


def _sessions_dir():
    output_folder = app_config.get_output_folder(app_config.load_config())
    sessions_dir = Path(output_folder) / "Sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def _session_path(session_id):
    return _sessions_dir() / f"{session_id}.json"


def _atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def start_session(preset_label):
    """Creates a new session record and returns its session_id. Call
    once at the top of each run_live_sermon() call."""
    now = datetime.now()

    # Microseconds included so two sessions starting within the same
    # second (realistically only possible in automated tests, not real
    # usage) get distinct filenames instead of one silently overwriting
    # the other's stats file.
    session_id = now.strftime("%Y-%m-%d_%H-%M-%S_%f")
    label = f"{now.strftime('%Y-%m-%d %H:%M')} {preset_label}".strip()

    record = {
        "session_id": session_id,
        "label": label,
        "preset": preset_label,
        "started_at": now.isoformat(),
        "ended_at": None,
        "thoughts_analyzed": 0,
        "ignored_count": 0,
        "possible_count": 0,
        "saved_count": 0,
        "total_score": 0,
        "phrase_hit_counts": {},
        "admin_phrase_hit_counts": {},
        "saved_clips": [],
    }

    _atomic_write_json(_session_path(session_id), record)

    return session_id


def record_thought(session_id, status, score, matched_phrases=None, admin_matched_phrases=None):
    """Call once per completed thought, regardless of outcome - status
    is "ignored" | "possible" | "saved". Recording every outcome (not
    just what got saved) is what makes the stats useful for tuning:
    seeing how many "possible" clips barely missed the save threshold,
    or which admin phrases are dragging good moments down."""
    record = _read_json(_session_path(session_id))

    if not record:
        return

    record["thoughts_analyzed"] += 1
    record["total_score"] += score

    count_key = {
        "ignored": "ignored_count",
        "possible": "possible_count",
        "saved": "saved_count",
    }.get(status)

    if count_key:
        record[count_key] = record.get(count_key, 0) + 1

    for phrase in (matched_phrases or []):
        record["phrase_hit_counts"][phrase] = record["phrase_hit_counts"].get(phrase, 0) + 1

    for phrase in (admin_matched_phrases or []):
        record["admin_phrase_hit_counts"][phrase] = record["admin_phrase_hit_counts"].get(phrase, 0) + 1

    _atomic_write_json(_session_path(session_id), record)


def record_saved_clip(session_id, clip_path):
    record = _read_json(_session_path(session_id))

    if not record:
        return

    record["saved_clips"].append(str(clip_path))

    _atomic_write_json(_session_path(session_id), record)


def end_session(session_id):
    record = _read_json(_session_path(session_id))

    if not record:
        return

    record["ended_at"] = datetime.now().isoformat()

    _atomic_write_json(_session_path(session_id), record)


def rename_session(session_id, new_label):
    record = _read_json(_session_path(session_id))

    if not record:
        return False

    record["label"] = new_label

    _atomic_write_json(_session_path(session_id), record)

    return True


def get_session(session_id):
    return _read_json(_session_path(session_id))


def delete_session(session_id):
    path = _session_path(session_id)

    if path.exists():
        path.unlink()
        return True

    return False


def list_sessions():
    """All session records, newest first."""
    sessions = []

    for path in _sessions_dir().glob("*.json"):
        record = _read_json(path)
        if record:
            sessions.append(record)

    sessions.sort(key=lambda record: record.get("started_at", ""), reverse=True)

    return sessions


def average_score(record):
    count = record.get("thoughts_analyzed", 0)

    if not count:
        return 0.0

    return record.get("total_score", 0) / count


def top_phrases(record, key="phrase_hit_counts", limit=10):
    counts = record.get(key, {})

    return sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
