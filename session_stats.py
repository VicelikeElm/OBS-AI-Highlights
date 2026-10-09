# -*- coding: utf-8 -*-
"""Per-session capture statistics and a lightweight session registry.

A session corresponds to one continuous AI or game-event capture run.
This is a metadata/stats layer only - it does NOT move or
reorganize the actual Raw/Verified/Review/Ready clip files, which stay
in their existing flat, already-proven folder structure. Sessions are
for browsing history and tuning against detection data, game events,
rounds, achievements, and saved clips.

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
    """Create a general capture session record and return its session_id."""
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


def start_game_session(game_name, profile_name):
    """Create a session record for one continuous game-event capture."""
    session_id = start_session(f"{game_name} game events")
    record = _read_json(_session_path(session_id))
    record["capture_mode"] = "game_events"
    record["game_name"] = game_name
    record["game_profile"] = profile_name
    record["game_event_counts"] = {}
    record["rounds_completed"] = 0
    record["notable_achievements"] = []
    record["game_clips_saved"] = 0
    _atomic_write_json(_session_path(session_id), record)
    return session_id


def set_game_session_profile(session_id, game_name, profile_name):
    """Attach game identity and counters to an existing AI capture session."""
    record = _read_json(_session_path(session_id))
    if not record:
        return
    record["game_name"] = game_name
    record["game_profile"] = profile_name
    record.setdefault("game_event_counts", {})
    record.setdefault("rounds_completed", 0)
    record.setdefault("notable_achievements", [])
    record.setdefault("game_clips_saved", 0)
    _atomic_write_json(_session_path(session_id), record)


def record_game_event(session_id, event, snapshot):
    """Persist recognized event totals and notable round achievements."""
    record = _read_json(_session_path(session_id))
    if not record:
        return

    event_type = str(event.get("type", "")).upper()
    counts = record.setdefault("game_event_counts", {})
    counts[event_type] = counts.get(event_type, 0) + 1
    if event_type == "ROUND_WIN":
        record["rounds_completed"] = record.get("rounds_completed", 0) + 1

    achievements = record.setdefault("notable_achievements", [])
    for achievement in snapshot.get("achievements", []):
        if achievement not in achievements:
            achievements.append(achievement)

    _atomic_write_json(_session_path(session_id), record)


def record_game_clip(session_id, clip_path):
    """Record a saved event-triggered game clip in its capture session."""
    record = _read_json(_session_path(session_id))
    if not record:
        return

    record["saved_clips"].append(str(clip_path))
    record["saved_count"] = record.get("saved_count", 0) + 1
    record["game_clips_saved"] = record.get("game_clips_saved", 0) + 1
    _atomic_write_json(_session_path(session_id), record)


def record_game_clip_count(session_id):
    """Count a game-triggered clip already recorded by another session path."""
    record = _read_json(_session_path(session_id))
    if not record:
        return
    record["game_clips_saved"] = record.get("game_clips_saved", 0) + 1
    _atomic_write_json(_session_path(session_id), record)


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
