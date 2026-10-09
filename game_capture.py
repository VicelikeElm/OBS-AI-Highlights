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
import session_stats

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".m4v", ".ts"}
REPLAY_WAIT_SECONDS = 30.0


def build_clip_context(snapshot, trigger_events, before_seconds=20, after_seconds=5):
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
        "clip_window": {
            "before_seconds": before_seconds,
            "after_seconds": after_seconds,
        },
    }


def clip_save_deadline(event_time, after_seconds):
    """Wait for the configured post-event footage before saving the replay."""
    return event_time + max(float(after_seconds), 0.0)


def should_trigger_event_clip(event, selected_event_types):
    return str(event.get("type", "")).upper() in selected_event_types


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
    interval = 1.0
    client = _connect_obs(config)
    monitor = None
    tracker = None
    active_game = None
    active_session_id = None
    pending_batches = []
    active_before_seconds = 20
    active_after_seconds = 5
    selected_event_types = set()
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
                        "process_names": app_config.get_game_profile_settings(
                            config, "custom"
                        ).get("process_names", []),
                    } if selected_game == "custom" else None,
                    profile_overrides=config.get("game_profiles", {}),
                )
                if detected is None:
                    monitor = None
                    tracker = None
                    if active_session_id:
                        session_stats.end_session(active_session_id)
                        active_session_id = None
                    active_game = None
                elif (
                    active_game is None
                    or detected["game_name"] != active_game["game_name"]
                ):
                    if active_session_id:
                        session_stats.end_session(active_session_id)
                    settings_key = (
                        "custom"
                        if detected["profile_name"] == "Custom"
                        else detected["game_name"]
                    )
                    profile_settings = app_config.get_game_profile_settings(
                        config,
                        settings_key,
                    )
                    if (
                        settings_key not in config.get("game_profiles", {})
                        and selected_game == "auto"
                    ):
                        profile_settings = app_config.get_game_profile_settings(
                            config,
                            "auto",
                        )
                    interval = max(float(profile_settings["ocr_interval"]), 0.25)
                    selected_event_types = set(profile_settings["event_clip_types"])
                    active_before_seconds = int(profile_settings["clip_before_seconds"])
                    active_after_seconds = int(profile_settings["clip_after_seconds"])
                    active_game = detected
                    tracker = RoundTracker(
                        detected["game_name"],
                        detected["profile_name"],
                    )
                    region = {
                        "left": float(profile_settings["ocr_left"]),
                        "top": float(profile_settings["ocr_top"]),
                        "width": float(profile_settings["ocr_width"]),
                        "height": float(profile_settings["ocr_height"]),
                    }
                    monitor = GameEventMonitor(
                        detected,
                        region=region,
                        player_name=profile_settings["player_name"],
                        tesseract_cmd=profile_settings["tesseract_cmd"],
                    )
                    active_session_id = session_stats.start_game_session(
                        detected["game_name"],
                        detected["profile_name"],
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
                    session_stats.record_game_event(active_session_id, event, snapshot)
                    if should_trigger_event_clip(event, selected_event_types):
                        event_time = time.monotonic()
                        batch = next(
                            (
                                candidate
                                for candidate in reversed(pending_batches)
                                if candidate["session_id"] == active_session_id
                            ),
                            None,
                        )
                        if batch is None:
                            batch = {
                                "session_id": active_session_id,
                                "events": [],
                                "snapshot": snapshot,
                                "before_seconds": active_before_seconds,
                                "after_seconds": active_after_seconds,
                                "save_at": 0,
                            }
                            pending_batches.append(batch)
                        batch["events"].append(event)
                        batch["snapshot"] = snapshot
                        batch["save_at"] = max(
                            batch["save_at"],
                            clip_save_deadline(event_time, active_after_seconds),
                        )
                    print(f"Recognized {event['type']}: {event['text']}")

            ready_batch = next(
                (
                    batch
                    for batch in pending_batches
                    if time.monotonic() >= batch["save_at"]
                ),
                None,
            )
            if ready_batch is not None:
                context = build_clip_context(
                    ready_batch["snapshot"],
                    ready_batch["events"],
                    ready_batch["before_seconds"],
                    ready_batch["after_seconds"],
                )
                clip_path = save_event_clip(
                    client,
                    recording_folder,
                    output_folder,
                    context,
                    sequence,
                )
                session_stats.record_game_clip(ready_batch["session_id"], clip_path)
                sequence += 1
                print(f"Saved and tagged game clip: {clip_path}")
                pending_batches.remove(ready_batch)

            time.sleep(0.25)
    except KeyboardInterrupt:
        print("Game-event capture stopped.")
    finally:
        if active_session_id:
            session_stats.end_session(active_session_id)
        client.disconnect()
