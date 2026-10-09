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
import csv
import xml.etree.ElementTree as ET
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


def start_recording_timeline(
    game_name="",
    profile_name="",
    recording_file="",
    embed_chapters=False,
):
    """Create one marker timeline for an ordinary OBS recording."""
    label = f"{game_name} recording" if game_name else "OBS recording"
    session_id = start_session(label)
    record = _read_json(_session_path(session_id))
    record["capture_mode"] = "recording_timeline"
    record["game_name"] = game_name
    record["game_profile"] = profile_name
    record["recording_file"] = recording_file
    record["timeline_markers"] = []
    record["recording_duration_seconds"] = 0.0
    record["chapter_embedding"] = "pending" if embed_chapters else "disabled"
    record["embedded_chapters_count"] = 0
    _atomic_write_json(_session_path(session_id), record)
    return session_id


def record_timeline_marker(session_id, elapsed_seconds, event_type, label, game_name="", profile_name=""):
    """Append a recording-relative marker to a timeline session."""
    record = _read_json(_session_path(session_id))
    if not record:
        return False

    elapsed_seconds = max(0.0, float(elapsed_seconds))
    record.setdefault("timeline_markers", []).append({
        "elapsed_seconds": round(elapsed_seconds, 3),
        "time": _format_timeline_time(elapsed_seconds),
        "type": str(event_type or "MANUAL").upper(),
        "label": str(label or "Marker"),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
    if game_name:
        record["game_name"] = game_name
    if profile_name:
        record["game_profile"] = profile_name
    _atomic_write_json(_session_path(session_id), record)
    return True


def update_recording_timeline(
    session_id,
    recording_file="",
    game_name="",
    profile_name="",
    recording_duration_seconds=None,
):
    record = _read_json(_session_path(session_id))
    if not record or record.get("capture_mode") != "recording_timeline":
        return
    changed = False
    if recording_file and recording_file != record.get("recording_file"):
        record["recording_file"] = recording_file
        changed = True
    if game_name and game_name != record.get("game_name"):
        record["game_name"] = game_name
        changed = True
    if profile_name and profile_name != record.get("game_profile"):
        record["game_profile"] = profile_name
        changed = True
    if recording_duration_seconds is not None:
        duration = max(0.0, float(recording_duration_seconds))
        if duration != record.get("recording_duration_seconds"):
            record["recording_duration_seconds"] = duration
            changed = True
    if changed:
        _atomic_write_json(_session_path(session_id), record)


def record_native_chapter(session_id):
    record = _read_json(_session_path(session_id))
    if not record or record.get("capture_mode") != "recording_timeline":
        return
    previous_status = record.get("chapter_embedding")
    record["embedded_chapters_count"] = record.get("embedded_chapters_count", 0) + 1
    record["chapter_embedding"] = (
        "partial" if previous_status in ("failed", "partial") else "embedded"
    )
    _atomic_write_json(_session_path(session_id), record)


def record_native_chapter_failure(session_id, error):
    record = _read_json(_session_path(session_id))
    if not record or record.get("capture_mode") != "recording_timeline":
        return
    record["chapter_embedding"] = (
        "partial"
        if record.get("embedded_chapters_count", 0)
        else "failed"
    )
    record["chapter_embedding_error"] = str(error)
    _atomic_write_json(_session_path(session_id), record)


def _format_timeline_time(seconds):
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def export_timeline_csv(session_id, destination):
    """Export timeline markers as a simple, editor-friendly CSV."""
    record = get_session(session_id)
    if not record or record.get("capture_mode") != "recording_timeline":
        return False

    with Path(destination).open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=("time", "elapsed_seconds", "type", "label", "game"),
        )
        writer.writeheader()
        for marker in record.get("timeline_markers", []):
            writer.writerow({
                "time": marker.get("time", ""),
                "elapsed_seconds": marker.get("elapsed_seconds", ""),
                "type": marker.get("type", ""),
                "label": marker.get("label", ""),
                "game": record.get("game_name", ""),
            })
    return True


def _timeline_markers(record):
    return sorted(
        record.get("timeline_markers", []),
        key=lambda marker: float(marker.get("elapsed_seconds", 0)),
    )


def _marker_title(marker):
    event_type = str(marker.get("type", "EVENT")).strip()
    label = str(marker.get("label", "Marker")).strip()
    return f"{event_type}: {label}" if event_type else label


def _recording_duration(record, markers):
    return max(
        float(record.get("recording_duration_seconds", 0) or 0),
        max(
            (float(marker.get("elapsed_seconds", 0) or 0) for marker in markers),
            default=0,
        ) + 1,
        1,
    )


def _format_timecode(seconds, fps=30):
    total_frames = max(0, round(float(seconds) * fps))
    total_seconds, frames = divmod(total_frames, fps)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def _xml_text_export(record, markers):
    lines = [record.get("label") or "Recording timeline"]
    lines.extend(
        f"{marker.get('time') or _format_timeline_time(marker.get('elapsed_seconds', 0))} "
        f"{_marker_title(marker)}"
        for marker in markers
    )
    return "\n".join(lines) + "\n"


def _final_cut_xml_export(record, markers):
    duration = _recording_duration(record, markers)
    root = ET.Element("fcpxml", {"version": "1.10"})
    resources = ET.SubElement(root, "resources")
    ET.SubElement(resources, "format", {
        "id": "r1",
        "name": "FFVideoFormat1080p30",
        "frameDuration": "1/30s",
        "width": "1920",
        "height": "1080",
    })
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": "OBS AI Highlights"})
    project = ET.SubElement(
        event,
        "project",
        {"name": str(record.get("label") or "Recording timeline")},
    )
    sequence = ET.SubElement(project, "sequence", {
        "format": "r1",
        "duration": f"{duration:.3f}s",
        "tcStart": "0s",
        "tcFormat": "NDF",
        "audioLayout": "stereo",
        "audioRate": "48k",
    })
    spine = ET.SubElement(sequence, "spine")
    gap = ET.SubElement(spine, "gap", {
        "name": "Timeline markers",
        "offset": "0s",
        "start": "0s",
        "duration": f"{duration:.3f}s",
    })
    for marker in markers:
        ET.SubElement(gap, "marker", {
            "start": f"{max(0.0, float(marker.get('elapsed_seconds', 0))):.3f}s",
            "value": _marker_title(marker),
        })
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def _premiere_xml_export(record, markers):
    fps = 30
    duration_frames = round(_recording_duration(record, markers) * fps)
    root = ET.Element("xmeml", {"version": "5"})
    sequence = ET.SubElement(root, "sequence", {"id": "sequence-1"})
    ET.SubElement(sequence, "name").text = str(
        record.get("label") or "Recording timeline"
    )
    ET.SubElement(sequence, "duration").text = str(duration_frames)
    rate = ET.SubElement(sequence, "rate")
    ET.SubElement(rate, "timebase").text = str(fps)
    ET.SubElement(rate, "ntsc").text = "FALSE"
    timecode = ET.SubElement(sequence, "timecode")
    timecode_rate = ET.SubElement(timecode, "rate")
    ET.SubElement(timecode_rate, "timebase").text = str(fps)
    ET.SubElement(timecode_rate, "ntsc").text = "FALSE"
    ET.SubElement(timecode, "string").text = "00:00:00:00"
    ET.SubElement(timecode, "frame").text = "0"
    ET.SubElement(timecode, "displayformat").text = "NDF"
    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    track = ET.SubElement(video, "track")
    ET.SubElement(track, "enabled").text = "TRUE"
    ET.SubElement(track, "locked").text = "FALSE"
    audio = ET.SubElement(media, "audio")
    ET.SubElement(audio, "track")
    for marker in markers:
        marker_frame = round(float(marker.get("elapsed_seconds", 0)) * fps)
        marker_node = ET.SubElement(sequence, "marker")
        ET.SubElement(marker_node, "name").text = _marker_title(marker)
        ET.SubElement(marker_node, "comment").text = str(marker.get("label", ""))
        ET.SubElement(marker_node, "in").text = str(marker_frame)
        ET.SubElement(marker_node, "out").text = str(marker_frame)
        ET.SubElement(marker_node, "duration").text = "1"
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def _resolve_edl_export(record, markers):
    fps = 30
    lines = [
        f"TITLE: {str(record.get('label') or 'Recording timeline')}",
        "FCM: NON-DROP FRAME",
    ]
    for index, marker in enumerate(markers, start=1):
        marker_time = float(marker.get("elapsed_seconds", 0))
        record_in = _format_timecode(marker_time, fps)
        record_out = _format_timecode(marker_time + 1 / fps, fps)
        lines.append(
            f"{index:03d}  AX       V     C        "
            f"{record_in} {record_out} {record_in} {record_out}"
        )
        lines.append(f"* COMMENT: {_marker_title(marker)}")
    return "\n".join(lines) + "\n"


TIMELINE_EXPORT_FORMATS = {
    "Text": (".txt", "Text files", _xml_text_export),
    "Final Cut Pro XML": (".fcpxml", "Final Cut Pro XML files", _final_cut_xml_export),
    "Premiere Pro XML": (".xml", "Premiere Pro XML files", _premiere_xml_export),
    "DaVinci Resolve EDL": (".edl", "EDL files", _resolve_edl_export),
}


def export_timeline(session_id, destination, export_format):
    """Export timeline markers to Text, editor XML, EDL, or CSV."""
    record = get_session(session_id)
    if not record or record.get("capture_mode") != "recording_timeline":
        return False

    destination = Path(destination)
    markers = _timeline_markers(record)
    if export_format == "CSV":
        return export_timeline_csv(session_id, destination)
    exporter = TIMELINE_EXPORT_FORMATS.get(export_format)
    if exporter is None:
        raise ValueError(f"Unsupported timeline export format: {export_format}")
    content = exporter[2](record, markers)
    destination.write_text(content, encoding="utf-8")
    return True


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
