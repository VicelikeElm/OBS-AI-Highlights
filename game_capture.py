"""Lightweight OBS Replay Buffer capture triggered by recognized game events."""

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

import obsws_python as obs

import config as app_config
import game_detector
from game_events import GameEventMonitor
from round_tracker import RoundTracker

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".m4v", ".ts"}
EVENT_BATCH_SECONDS = 2.0
REPLAY_WAIT_SECONDS = 30.0


def build_clip_context(snapshot, trigger_events):
    """Build the tag sidecar payload for one OBS Replay Buffer clip."""
    tags = list(dict.fromkeys(
        [event["type"] for event in trigger_events]
        + list(snapshot.get("achievements", []))
    ))
    return {
        "game": snapshot["game"],
        "profile": snapshot["profile"],
        "round": snapshot["round"],
        "score": snapshot["score"],
        "achievements": list(snapshot.get("achievements", [])),
        "tags": tags,
        "events": list(snapshot.get("events", [])),
        "trigger_events": list(trigger_events),
        "capture_source": "game_event_ocr",
    }


def _replay_files(folder):
    return {
        path.resolve()
        for path in Path(folder).iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    }


def _wait_for_replay(folder, previous_files, timeout=REPLAY_WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    stable_sizes = {}

    while time.monotonic() < deadline:
        new_files = _replay_files(folder) - previous_files
        for path in sorted(new_files, key=lambda item: item.stat().st_mtime, reverse=True):
            size = path.stat().st_size
            if size > 0 and stable_sizes.get(path) == size:
                return path
            stable_sizes[path] = size
        time.sleep(0.5)

    return None


def save_event_clip(client, recording_folder, output_folder, context, sequence):
    """Save the OBS replay, move it into Raw, and write its game tags."""
    recording_folder = Path(recording_folder)
    raw_folder = Path(output_folder) / "Raw"
    transcript_folder = Path(output_folder) / "Transcripts"
    if not recording_folder.is_dir():
        raise FileNotFoundError(f"OBS recording folder is unavailable: {recording_folder}")
    raw_folder.mkdir(parents=True, exist_ok=True)
    transcript_folder.mkdir(parents=True, exist_ok=True)

    previous_files = _replay_files(recording_folder)
    client.save_replay_buffer()
    replay_path = _wait_for_replay(recording_folder, previous_files)
    if replay_path is None:
        raise TimeoutError(
            f"OBS saved a replay, but no completed video appeared in {recording_folder}."
        )

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_name = f"{timestamp}_game_{sequence:03d}"
    destination = raw_folder / f"{base_name}{replay_path.suffix.lower()}"
    while destination.exists():
        sequence += 1
        base_name = f"{timestamp}_game_{sequence:03d}"
        destination = raw_folder / f"{base_name}{replay_path.suffix.lower()}"

    shutil.move(str(replay_path), str(destination))

    sidecar = transcript_folder / f"{base_name}_game.json"
    temporary_sidecar = sidecar.with_suffix(sidecar.suffix + ".tmp")
    temporary_sidecar.write_text(
        json.dumps(context, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_sidecar.replace(sidecar)

    tags = ", ".join(context.get("tags", [])) or "Game event"
    transcript = transcript_folder / f"{base_name}.txt"
    transcript.write_text(
        f"Game event clip\nGame: {context['game']}\nTags: {tags}\n",
        encoding="utf-8",
    )
    return destination


def _connect_obs(config):
    return obs.ReqClient(
        host=config.get("obs_host", "127.0.0.1"),
        port=int(config.get("obs_port", 4455)),
        password=app_config.get_obs_password(),
        timeout=5,
    )


def main():
    config = app_config.load_config()
    if not config.get("game_events_enabled", True):
        raise RuntimeError(
            "Game-event capture is disabled. Enable it in Settings > Advanced > Game Events."
        )

    selected_game = config.get("game_selection", "auto")
    recording_folder = config.get(
        "recording_folder",
        app_config.DEFAULTS["recording_folder"],
    )
    output_folder = app_config.get_output_folder(config)
    interval = max(float(config.get("game_ocr_interval", 1.0)), 0.25)
    client = _connect_obs(config)
    monitor = None
    tracker = None
    active_game = None
    pending_events = []
    pending_snapshot = None
    save_at = None
    next_game_check = 0.0
    next_obs_check = 0.0
    next_ocr_poll = 0.0
    sequence = 1

    print("Lightweight game-event capture started; Whisper and audio capture are off.")
    print("Waiting for OBS Replay Buffer and the selected game...")

    try:
        while True:
            now = time.monotonic()

            if now >= next_obs_check:
                status = client.get_replay_buffer_status()
                if not status.output_active:
                    client.start_replay_buffer()
                    print("Started OBS Replay Buffer.")
                next_obs_check = now + 5.0

            if now >= next_game_check:
                detected = game_detector.detect_running_game(
                    selected_game=selected_game if selected_game != "auto" else None,
                    custom_profile={
                        "game_name": config.get("custom_game_name", ""),
                        "process_name": config.get("custom_game_process_name", ""),
                    } if selected_game == "custom" else None,
                )
                if detected is None:
                    monitor = None
                    tracker = None
                    active_game = None
                elif (
                    active_game is None
                    or detected["game_name"] != active_game["game_name"]
                ):
                    active_game = detected
                    tracker = RoundTracker(
                        detected["game_name"],
                        detected["profile_name"],
                    )
                    region = {
                        "left": float(config.get("game_ocr_left", 0.70)),
                        "top": float(config.get("game_ocr_top", 0.04)),
                        "width": float(config.get("game_ocr_width", 0.29)),
                        "height": float(config.get("game_ocr_height", 0.30)),
                    }
                    monitor = GameEventMonitor(
                        detected,
                        region=region,
                        player_name=config.get("game_player_name", ""),
                        tesseract_cmd=config.get("game_tesseract_cmd", ""),
                    )
                    next_ocr_poll = 0.0
                    print(
                        f"Detected {detected['game_name']}; watching its on-screen event feed."
                    )
                next_game_check = now + 3.0

            if monitor is not None and tracker is not None and now >= next_ocr_poll:
                try:
                    events = monitor.poll_events()
                except Exception as error:
                    raise RuntimeError(f"Game-event OCR failed: {error}") from error

                next_ocr_poll = time.monotonic() + interval
                for event in events:
                    snapshot = tracker.apply_event(event)
                    if snapshot is None:
                        continue
                    pending_events.append(event)
                    pending_snapshot = snapshot
                    save_at = time.monotonic() + EVENT_BATCH_SECONDS
                    print(f"Recognized {event['type']}: {event['text']}")

            if pending_events and save_at is not None and time.monotonic() >= save_at:
                context = build_clip_context(pending_snapshot, pending_events)
                clip_path = save_event_clip(
                    client,
                    recording_folder,
                    output_folder,
                    context,
                    sequence,
                )
                sequence += 1
                print(f"Saved and tagged game clip: {clip_path}")
                pending_events = []
                pending_snapshot = None
                save_at = None

            time.sleep(0.25)
    except KeyboardInterrupt:
        print("Game-event capture stopped.")
    finally:
        client.disconnect()
