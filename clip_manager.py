# -*- coding: utf-8 -*-
"""Unified per-clip view across the Raw / Transcripts / Verified / Review /
Ready pipeline, for the Clips tab in app.py.

Every clip that's ever saved has a raw video in Raw/ that stays there for
its whole life (verify_clips.py/render_clips.py never move or delete it) -
so Raw/ is the canonical list of clips, and everything else here is joined
onto it by filename stem ("base_name"): the plain-text candidate transcript
highlight_engine.py wrote in Transcripts/ (score, reasons, duration, live
transcript), the richer JSON verify_clips.py wrote in Verified/ or Review/
(status, verified transcript, trim points), and whether it's been rendered
into Ready/.

This module only reads config and (for the Render action) calls into
render_clips.py, which - unlike highlight_engine.py/verify_clips.py - has
no Whisper/OBS import weight (see its own module docstring/imports), so
it's safe to import directly into the always-running GUI process instead
of needing a subprocess worker the way Capture/Verify/Render-all do.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import config as app_config
import render_clips

CONFIG = app_config.load_config()

SHORTS_ROOT = app_config.get_output_folder(CONFIG)

RAW_FOLDER = SHORTS_ROOT / "Raw"
TRANSCRIPT_FOLDER = SHORTS_ROOT / "Transcripts"
VERIFIED_FOLDER = SHORTS_ROOT / "Verified"
REVIEW_FOLDER = SHORTS_ROOT / "Review"
READY_FOLDER = SHORTS_ROOT / "Ready"
THUMBNAIL_FOLDER = SHORTS_ROOT / ".thumbnails"

THUMBNAIL_WIDTH = 220

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".m4v", ".ts")

SCORE_IN_FILENAME_PATTERN = re.compile(r"_Score_(\d+)")
DURATION_PATTERN = re.compile(r"^Thought duration:\s*([\d.]+)", re.MULTILINE)
REASONS_BLOCK_PATTERN = re.compile(r"Reasons:\n(.*?)\n\nLIVE WHISPER TRANSCRIPT", re.DOTALL)
LIVE_TRANSCRIPT_PATTERN = re.compile(r"LIVE WHISPER TRANSCRIPT\n-+\n(.*)", re.DOTALL)

CLIP_LABEL_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})_AI_Clip_(\d+)_Score_\d+$"
)


def format_clip_label(base_name):
    """"2026-09-15_09-10-00_AI_Clip_03_Score_91" -> "2026-09-15 09:10:00
    (#03)" for the Clips tab's list - falls back to the raw base_name
    verbatim for anything that doesn't match the standard naming (there
    isn't one today, but this must never raise/crash the tab)."""
    match = CLIP_LABEL_PATTERN.match(base_name)

    if not match:
        return base_name

    date, hour, minute, second, clip_number = match.groups()

    return f"{date} {hour}:{minute}:{second} (#{clip_number})"


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _related_files(folder, base_name):
    """Every file in `folder` belonging to this clip - an exact
    "base_name.ext" (the raw video, a .json/.srt) or a
    "base_name_something.ext" (e.g. "_verified.txt", "_SHORT.mp4"). The
    boundary character right after base_name (a "." or "_") is required
    so a clip whose base_name happens to be a prefix of another's (e.g.
    a score of "8" vs "87" sharing "..._Score_8") can never match the
    wrong clip's files."""
    if not folder.exists():
        return []

    return [
        path
        for path in folder.iterdir()
        if path.name.startswith(base_name + ".") or path.name.startswith(base_name + "_")
    ]


def _find_raw_video(base_name):
    if not RAW_FOLDER.exists():
        return None

    for extension in VIDEO_EXTENSIONS:
        candidate = RAW_FOLDER / (base_name + extension)
        if candidate.exists():
            return candidate

    return None


def _score_from_filename(video_path):
    match = SCORE_IN_FILENAME_PATTERN.search(video_path.name)
    return int(match.group(1)) if match else None


def _parse_candidate_transcript(path):
    """Best-effort parse of the plain-text candidate file
    highlight_engine.py's save_candidate_transcript() writes for every
    saved clip - reasons, thought duration, and the live (unverified)
    transcript. Never raises; a missing/unreadable/unexpected-format
    file just yields fewer fields, since this is read-only display data."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}

    result = {}

    duration_match = DURATION_PATTERN.search(text)
    if duration_match:
        result["duration"] = float(duration_match.group(1))

    reasons_match = REASONS_BLOCK_PATTERN.search(text)
    if reasons_match:
        result["reasons"] = [
            line[2:].strip() for line in reasons_match.group(1).splitlines() if line.startswith("- ")
        ]

    transcript_match = LIVE_TRANSCRIPT_PATTERN.search(text)
    if transcript_match:
        result["live_transcript"] = transcript_match.group(1).strip()

    return result


def get_clip(base_name, raw_video=None):
    raw_video = raw_video or _find_raw_video(base_name)

    candidate_path = TRANSCRIPT_FOLDER / (base_name + ".txt")
    candidate_data = _parse_candidate_transcript(candidate_path) if candidate_path.exists() else {}

    verified_json_path = VERIFIED_FOLDER / (base_name + ".json")
    review_json_path = REVIEW_FOLDER / (base_name + ".json")

    metadata = None
    metadata_path = None

    if verified_json_path.exists():
        metadata = _read_json(verified_json_path)
        metadata_path = verified_json_path
    elif review_json_path.exists():
        metadata = _read_json(review_json_path)
        metadata_path = review_json_path

    status = (metadata or {}).get("status", "Review").title() if metadata else "Pending"

    rendered_path = READY_FOLDER / (base_name + "_SHORT.mp4")
    rendered = rendered_path.exists()

    transcript = (
        (metadata or {}).get("verified_matched_transcript")
        or candidate_data.get("live_transcript")
        or (metadata or {}).get("live_transcript")
        or ""
    )

    duration = (metadata or {}).get("final_duration")
    if duration is None:
        duration = candidate_data.get("duration")

    return {
        "base_name": base_name,
        "raw_video": str(raw_video) if raw_video else None,
        "score": _score_from_filename(raw_video) if raw_video else None,
        "status": status,
        "duration": duration,
        "trim_start": (metadata or {}).get("trim_start"),
        "trim_end": (metadata or {}).get("trim_end"),
        "reasons": candidate_data.get("reasons", []),
        "transcript": transcript,
        "rendered": rendered,
        "rendered_path": str(rendered_path) if rendered else None,
        "metadata_path": str(metadata_path) if metadata_path else None,
    }


def list_clips():
    """One record per raw clip ever saved, newest first (base_name starts
    with a sortable "YYYY-MM-DD_HH-MM-SS" timestamp)."""
    if not RAW_FOLDER.exists():
        return []

    clips = []

    for path in RAW_FOLDER.iterdir():
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            clips.append(get_clip(path.stem, raw_video=path))

    clips.sort(key=lambda clip: clip["base_name"], reverse=True)

    return clips


def _extract_frame(source_path, seek_seconds, thumbnail_path):
    command = [
        render_clips.FFMPEG_EXECUTABLE,
        "-y",
        "-ss", f"{max(seek_seconds, 0.0):.3f}",
        "-i", str(source_path),
        "-frames:v", "1",
        "-vf", f"scale={THUMBNAIL_WIDTH}:-1",
        str(thumbnail_path),
    ]

    try:
        result = subprocess.run(command, capture_output=True, timeout=15)
    except Exception:
        return False

    return result.returncode == 0 and thumbnail_path.exists()


def get_thumbnail(base_name, force=False):
    """A small PNG preview frame for this clip, generated via ffmpeg and
    cached in THUMBNAIL_FOLDER - the rendered Short if there is one
    (most representative of the actual output), otherwise the raw
    source clip. Regenerates automatically if the source file is newer
    than the cached thumbnail (e.g. after a re-render or a trim edit),
    not just once ever. Returns None if there's nothing to generate a
    thumbnail from, or if ffmpeg fails - never raises, since this is
    best-effort display data, not something that should break the tab."""
    clip = get_clip(base_name)

    if clip["rendered_path"]:
        source_path = Path(clip["rendered_path"])
        seek_seconds = 1.0
    elif clip["raw_video"]:
        source_path = Path(clip["raw_video"])
        trim_start = clip["trim_start"]
        seek_seconds = (trim_start if trim_start is not None else 0.0) + 1.0
    else:
        return None

    if not source_path.exists():
        return None

    thumbnail_path = THUMBNAIL_FOLDER / (base_name + ".png")

    if (
        not force
        and thumbnail_path.exists()
        and thumbnail_path.stat().st_mtime >= source_path.stat().st_mtime
    ):
        return thumbnail_path

    THUMBNAIL_FOLDER.mkdir(parents=True, exist_ok=True)

    if _extract_frame(source_path, seek_seconds, thumbnail_path):
        return thumbnail_path

    # A short clip (or a seek point past its own end) can leave nothing
    # to extract at the offset above - fall back to the very first frame
    # rather than showing nothing at all.
    if seek_seconds > 0 and _extract_frame(source_path, 0.0, thumbnail_path):
        return thumbnail_path

    return None


def play_clip(base_name):
    """Opens the clip in the OS's default video player - the finished
    vertical Short if it's been rendered, otherwise the raw source clip."""
    clip = get_clip(base_name)

    path = clip["rendered_path"] or clip["raw_video"]

    if not path:
        return False, "No video file found for this clip."

    try:
        os.startfile(path)
    except Exception as error:
        return False, f"Could not open video: {error}"

    return True, None


def approve_clip(base_name):
    """Review -> Verified: flips the JSON's status and moves every file
    sharing this base_name from Review to Verified. Non-destructive -
    files are relocated, not deleted, the same two folders
    verify_clips.py itself already separates clips into."""
    review_json_path = REVIEW_FOLDER / (base_name + ".json")

    if not review_json_path.exists():
        return False, "No Review record found for this clip."

    metadata = _read_json(review_json_path) or {}
    metadata["status"] = "VERIFIED"

    VERIFIED_FOLDER.mkdir(parents=True, exist_ok=True)

    for path in _related_files(REVIEW_FOLDER, base_name):
        path.replace(VERIFIED_FOLDER / path.name)

    (VERIFIED_FOLDER / (base_name + ".json")).write_text(
        json.dumps(metadata, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )

    return True, "Approved - moved to Verified."


def reject_clip(base_name):
    """Marks a clip's own JSON (wherever it currently lives - Verified or
    Review) as REJECTED in place. render_clips.py's get_verified_jobs()
    already only renders status == "VERIFIED", so a rejected clip is
    simply excluded from rendering, exactly like a Review clip - nothing
    is deleted, so this is easy to walk back."""
    for folder in (VERIFIED_FOLDER, REVIEW_FOLDER):
        json_path = folder / (base_name + ".json")

        if not json_path.exists():
            continue

        metadata = _read_json(json_path) or {}
        metadata["status"] = "REJECTED"

        json_path.write_text(
            json.dumps(metadata, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )

        return True, "Rejected."

    return False, "No Verified/Review record found for this clip."


def update_trim(base_name, trim_start, trim_end):
    """Overrides a clip's own trim points - for fixing an auto-detected
    trim that cut off a word or left in too much dead air, without
    needing to hand-edit the JSON file. Only meaningful for a clip
    that's already been verified (has trim data to override at all);
    the raw video itself is never touched, so this stays cheap and
    re-render-friendly - Render picks up the new points immediately."""
    for folder in (VERIFIED_FOLDER, REVIEW_FOLDER):
        json_path = folder / (base_name + ".json")

        if not json_path.exists():
            continue

        metadata = _read_json(json_path) or {}

        if metadata.get("trim_start") is None or metadata.get("trim_end") is None:
            return False, "This clip has no existing trim data to override."

        try:
            trim_start = float(trim_start)
            trim_end = float(trim_end)
        except (TypeError, ValueError):
            return False, "Start and end must be numbers."

        if trim_start < 0:
            return False, "Start can't be negative."

        if trim_end <= trim_start:
            return False, "End must be after start."

        metadata["trim_start"] = trim_start
        metadata["trim_end"] = trim_end
        metadata["final_duration"] = trim_end - trim_start

        json_path.write_text(
            json.dumps(metadata, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )

        return True, "Trim updated."

    return False, "No Verified/Review record found for this clip."


def render_clip(base_name):
    """Renders one specific clip right now. Reuses render_clips.py's own
    render_job() so the result is byte-identical to a normal batch render
    (same ffmpeg command, same ready-to-post metadata sidecar)."""
    metadata_path = VERIFIED_FOLDER / (base_name + ".json")
    review_json_path = REVIEW_FOLDER / (base_name + ".json")

    if metadata_path.exists():
        metadata = _read_json(metadata_path)

        if not metadata or metadata.get("status") != "VERIFIED":
            status_label = (metadata or {}).get("status", "").title() or "unknown"
            return False, f"This clip's status is {status_label}, not Verified - it can't be rendered."

    elif review_json_path.exists():
        # A real, different fix than "Verified but wrong status" below -
        # Approve only works on a clip already sitting in Review, so
        # this is the one case where that's actually the right next step.
        return False, "This clip is in Review, not Verified yet - approve it first."

    else:
        # No record in either folder at all - it's never been verified,
        # so Approve (Review -> Verified) doesn't apply here; it needs
        # the Verify Clips step first (Run tab, or Auto Verify).
        return False, "This clip hasn't been verified yet - run Verify Clips first."

    raw_video = _find_raw_video(base_name)

    if raw_video is None:
        return False, "Raw video not found."

    srt_path = VERIFIED_FOLDER / (base_name + ".srt")

    if not srt_path.exists():
        return False, "No caption file (.srt) found for this clip."

    trim_start = metadata.get("trim_start")
    trim_end = metadata.get("trim_end")

    if trim_start is None or trim_end is None:
        return False, "Trim information missing for this clip."

    job = {
        "base_name": base_name,
        "raw_video": raw_video,
        "srt": srt_path,
        "json": metadata_path,
        "trim_start": float(trim_start),
        "trim_end": float(trim_end),
        "transcript": metadata.get("verified_matched_transcript") or metadata.get("live_transcript", ""),
    }

    render_clips.READY_FOLDER.mkdir(parents=True, exist_ok=True)

    if render_clips.render_job(job):
        return True, "Rendered."

    return False, "FFmpeg render failed - see the console/log for details."


def encoder_fallback_active():
    """True if rendering is using CPU/software encoding even though
    "nvenc" is the configured setting - i.e. render_clips.py's own
    startup probe found NVENC isn't actually usable on this machine
    (see its _nvenc_available()). Used by the Clips tab to show a
    one-time heads-up, since a render triggered from there runs
    in-process (no console) and would otherwise silently just be
    slower with no visible explanation."""
    return render_clips.RENDER_ENCODER == "nvenc" and render_clips.encoder_in_use() == "cpu"


def delete_clip(base_name):
    """Permanently deletes every file across every stage that shares this
    base_name - the raw video, its candidate transcript, Verified/Review
    records, and a rendered Short + metadata sidecar, if any. The one
    genuinely destructive action here; Reject is the safe, reversible
    alternative for "this clip isn't good enough"."""
    removed_any = False

    for folder in (
        RAW_FOLDER,
        TRANSCRIPT_FOLDER,
        VERIFIED_FOLDER,
        REVIEW_FOLDER,
        READY_FOLDER,
        THUMBNAIL_FOLDER,
    ):
        for path in _related_files(folder, base_name):
            path.unlink()
            removed_any = True

    if not removed_any:
        return False, "No files found for this clip."

    return True, "Deleted."
