# -*- coding: utf-8 -*-
"""OBS AI Highlights - unified app.

Single entry point, whether run from source or packaged as one frozen
exe. With no arguments it opens the GUI (Run tab + embedded Settings
tab). With `--worker <role>` it acts as a worker process instead,
running one of the three engine scripts' main() and exiting when done -
this is how the GUI launches live capture / verification / rendering,
and how highlight_engine.py's own post-service auto-chaining relaunches
itself once frozen (see config.worker_launch_command()).

    python app.py                  # GUI
    python app.py --worker capture # runs highlight_engine.main()
    python app.py --worker verify  # runs verify_clips.main()
    python app.py --worker render  # runs render_clips.main()
"""

import os
import sys
import json
import importlib
import queue
import shutil
import tempfile
import threading
import subprocess
import urllib.request
import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

try:
    import psutil
except Exception:
    psutil = None

import config as app_config
import clip_manager
import game_detector
import ui_theme
import session_stats
from settings_ui import SettingsUI
from version import APP_VERSION, GITHUB_REPO

WORKER_LABELS = {
    "capture": "Highlight Capture",
    "verify": "Verify Clips",
    "render": "Render Clips",
}

CLIP_STATUS_FILTER_VALUES = ["Pending", "Review", "Verified", "Rejected"]

CLIP_SORT_KEY_FUNCS = {
    "clip": lambda clip: clip["base_name"],
    "score": lambda clip: clip["score"] if clip["score"] is not None else -1,
    "status": lambda clip: clip["status"],
    "duration": lambda clip: clip["duration"] if clip["duration"] is not None else -1,
    "rendered": lambda clip: clip["rendered"],
}


def _parse_version(text):
    """"v1.2.3" / "1.2.3" -> (1, 2, 3), tolerant of stray characters so a
    malformed tag never crashes the comparison, just sorts low."""
    text = text.strip().lstrip("vV")
    parts = []

    for piece in text.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)

    return tuple(parts)


def _select_update_asset(assets):
    """Prefer the small app-only update; older releases still use installers."""
    for prefix in ("OBSAIHighlights-Update-", "OBSAIHighlights-Setup-"):
        for asset in assets:
            name = asset.get("name", "")
            if name.startswith(prefix) and name.endswith(".exe"):
                return asset.get("browser_download_url"), asset.get("size")
    return None, None


def _friendly_activity_message(text):
    """Return a short user-facing activity message for meaningful worker output."""
    line = text.strip()
    if not line:
        return None

    lower_line = line.lower()
    if line.startswith("--- Failed to start "):
        details = line[len("--- Failed to start "):].strip()
        if details.endswith("---"):
            details = details[:-3].rstrip()
        return f"Couldn't start {details}"
    if line.startswith("WARNING:"):
        return f"Needs attention: {line[len('WARNING:'):].strip()}"
    if line.startswith("ERROR:"):
        return f"Task error: {line[len('ERROR:'):].strip()}"
    if "traceback (most recent call last)" in lower_line:
        return "A task hit an unexpected error. Open technical details for more information."
    if any(word in lower_line for word in ("error:", "failed", "exception", "could not")):
        return f"Needs attention: {line}"

    if lower_line.startswith("waiting for obs"):
        return "Waiting for OBS. Open OBS and start its Replay Buffer to continue."
    if lower_line in {"obs is ready.", "obs websocket connected."}:
        return "Connected to OBS."
    if lower_line == "live whisper loaded.":
        return "Audio analysis is ready."
    if lower_line == "clip saved:":
        return "A highlight clip was saved."
    if line.startswith("Game detected:"):
        game_name = line[len("Game detected:"):].split("(profile:", 1)[0].strip().rstrip(".")
        return f"Game detected: {game_name}."
    if lower_line.startswith("game hud ocr started"):
        return "Game-event detection is ready."
    return None


def _summarize_obs_status(replay, recording, streaming):
    if replay:
        return "ready", "OBS is connected and Replay Buffer is on."
    if recording or streaming:
        return (
            "warning",
            "OBS is active, but Replay Buffer is off. Capture can try to start it automatically.",
        )
    return (
        "warning",
        "Replay Buffer, recording, and streaming are off. Start one before expecting clips; "
        "capture will wait until OBS is active.",
    )


def _check_folder_preflight(label, folder, allow_create=False):
    try:
        path = Path(str(folder)).expanduser()
        if path.exists():
            if not path.is_dir():
                return "warning", f"{label} is not a folder: {path}"
            if not os.access(path, os.R_OK | os.W_OK):
                return "warning", f"{label} isn't readable and writable: {path}"
            return "ready", f"Folder is available: {path}"

        if allow_create:
            parent = path.parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
            if parent.is_dir() and os.access(parent, os.R_OK | os.W_OK):
                return "ready", f"Folder will be created when needed: {path}"

        return "warning", f"{label} isn't available: {path}"
    except (OSError, TypeError, ValueError) as error:
        return "warning", f"Couldn't check {label}: {error}"


def _audio_device_for_preflight(devices, configured_name, is_microphone, fallback_index):
    exact = [device for device in devices if device.get("name", "") == configured_name]
    if exact:
        return exact[0]

    if is_microphone:
        partial = [
            device for device in devices
            if device.get("maxInputChannels", 0) > 0
            and not device.get("isLoopbackDevice", False)
        ]
    else:
        partial = [
            device for device in devices
            if "Headphones" in device.get("name", "")
            and "High Definition Audio Device" in device.get("name", "")
            and "Loopback" in device.get("name", "")
        ]
    if partial:
        return partial[0]

    try:
        fallback = devices[int(fallback_index)]
    except (IndexError, TypeError, ValueError):
        return None
    return fallback if fallback.get("maxInputChannels", 0) > 0 else None


def _check_audio_preflight(config):
    try:
        pyaudio = importlib.import_module("pyaudio")
        audio = pyaudio.PyAudio()
        try:
            devices = [
                audio.get_device_info_by_index(index)
                for index in range(audio.get_device_count())
            ]
        finally:
            audio.terminate()
    except Exception as error:
        return "warning", f"Couldn't check audio devices: {error}"

    source_type = config.get("audio_source_type", "loopback")
    roles = []
    if source_type in {"loopback", "both"}:
        roles.append(("loopback audio", config.get("audio_loopback_device_name", ""), False))
    if source_type in {"microphone", "both"}:
        roles.append(("microphone", config.get("audio_microphone_device_name", ""), True))
    if not roles:
        roles.append(("loopback audio", config.get("audio_loopback_device_name", ""), False))

    selected = []
    for label, configured_name, is_microphone in roles:
        device = _audio_device_for_preflight(
            devices,
            configured_name,
            is_microphone,
            config.get("audio_device_fallback_index", 0),
        )
        if device is None or device.get("maxInputChannels", 0) <= 0:
            return "warning", f"No usable {label} device was found. Choose one in Settings."
        selected.append(f"{label}: {device.get('name', 'unnamed device')}")

    return "ready", "Audio device is available (" + "; ".join(selected) + ")."


def _check_tesseract_preflight(config):
    if not config.get("game_events_enabled", True):
        return "optional", "Game-event detection is turned off."

    configured_command = str(config.get("game_tesseract_cmd", "")).strip()
    available = (
        Path(configured_command).is_file() or shutil.which(configured_command) is not None
        if configured_command
        else shutil.which("tesseract") is not None
    )
    if available:
        return "ready", "Tesseract is available for game-event OCR."
    return (
        "optional",
        "Game-event OCR needs Tesseract. Install it or set its path in Settings; "
        "audio-based highlights can still work.",
    )


def _check_obs_preflight(config=None):
    config = config or app_config.load_config()
    game_capture_mode = config.get("capture_mode", "game_events") == "game_events"
    host = str(config.get("obs_host", "127.0.0.1"))
    port = config.get("obs_port", 4455)
    client = None

    try:
        from obsws_python import ReqClient

        client = ReqClient(
            host=host,
            port=int(port),
            password=app_config.get_obs_password(),
            timeout=3,
        )
        replay = bool(getattr(client.get_replay_buffer_status(), "output_active", False))
        recording = bool(getattr(client.get_record_status(), "output_active", False))
        streaming = bool(getattr(client.get_stream_status(), "output_active", False))
        level, message = _summarize_obs_status(replay, recording, streaming)
        obs_check = ("OBS connection", level, message, "")
    except Exception as error:
        obs_check = (
            "OBS connection",
            "warning",
            f"Couldn't connect to OBS at {host}:{port}. Check that OBS is open and its "
            "WebSocket settings match. Capture can still wait for OBS.",
            str(error),
        )
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass

    recording_check = _check_folder_preflight(
        "OBS recording folder",
        config.get("recording_folder", app_config.DEFAULTS["recording_folder"]),
    )
    try:
        output_folder = app_config.get_output_folder(config)
        output_check = _check_folder_preflight("Output folder", output_folder, allow_create=True)
    except (OSError, TypeError, ValueError) as error:
        output_check = ("warning", f"Couldn't check clip output folder: {error}")
    if game_capture_mode:
        audio_level, audio_message = (
            "optional",
            "Not needed in Game events mode. Choose AI audio and transcript mode to use audio.",
        )
    else:
        audio_level, audio_message = _check_audio_preflight(config)

    tesseract_level, tesseract_message = _check_tesseract_preflight(config)
    if game_capture_mode:
        if not config.get("game_events_enabled", True):
            tesseract_level = "warning"
            tesseract_message = "Enable game-event OCR in Settings to capture game highlights."
        elif tesseract_level == "optional":
            tesseract_level = "warning"
            tesseract_message = (
                "Game events mode needs Tesseract OCR. Install it or set its path in "
                "Settings > Advanced > Game Events."
            )

    return [
        ("obs", "OBS connection", *obs_check[1:]),
        ("audio", "Audio input", audio_level, audio_message, ""),
        ("recording", "OBS recording folder", *recording_check, ""),
        ("output", "Clip output folder", *output_check, ""),
        ("ocr", "Game-event OCR (optional)", tesseract_level, tesseract_message, ""),
    ]


def _fetch_latest_release():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "OBS-AI-Highlights",
        },
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))

    tag_name = data.get("tag_name", "")
    html_url = data.get("html_url", "")

    asset_url, asset_size = _select_update_asset(data.get("assets", []))

    return tag_name, html_url, asset_url, asset_size


def _download_installer(asset_url, expected_size, progress_callback):
    request = urllib.request.Request(
        asset_url,
        headers={"User-Agent": "OBS-AI-Highlights"},
    )

    dest_path = Path(tempfile.gettempdir()) / Path(asset_url).name

    with urllib.request.urlopen(request, timeout=30) as response:
        downloaded = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                progress_callback(downloaded, expected_size)

    if expected_size and dest_path.stat().st_size != expected_size:
        raise RuntimeError("Downloaded file size doesn't match - it may be corrupted.")

    return dest_path


def run_worker(role):
    if role == "capture":
        if app_config.load_config().get("capture_mode") == "game_events":
            import game_capture
            game_capture.main()
            return
        import highlight_engine
        highlight_engine.main()
    elif role == "verify":
        import verify_clips
        verify_clips.main()
    elif role == "render":
        import render_clips
        render_clips.main()
    else:
        raise SystemExit(f"Unknown worker role: {role}")


class MainApp:
    def __init__(self, root):
        self.root = root
        self._first_run = not app_config.CONFIG_FILE.exists()
        self.root.title(f"OBS AI Highlights v{APP_VERSION}")
        self.root.geometry("880x700")
        self.root.minsize(780, 600)

        self._text_widget_colors = ui_theme.text_widget_colors(ui_theme.get_theme())

        self.active_process = None
        self.active_role = None
        self.log_queue = queue.Queue()
        self._preflight_check_running = False
        self.update_queue = queue.Queue()
        self.download_queue = queue.Queue()
        self.clip_render_queue = queue.Queue()
        self.clip_thumbnail_queue = queue.Queue()
        self._encoder_fallback_notice_shown = False
        self._latest_release_url = None
        self._latest_asset_url = None
        self._latest_asset_size = None

        notebook = ttk.Notebook(root)
        self.main_notebook = notebook
        notebook.pack(fill="both", expand=True, padx=12, pady=12)

        run_tab = ttk.Frame(notebook, padding=12)
        clips_tab = ttk.Frame(notebook, padding=12)
        sessions_tab = ttk.Frame(notebook, padding=12)
        settings_tab = ttk.Frame(notebook, padding=12)
        updates_tab = ttk.Frame(notebook, padding=12)

        notebook.add(run_tab, text="Run")
        notebook.add(clips_tab, text="Clips")
        notebook.add(sessions_tab, text="Sessions")
        notebook.add(settings_tab, text="Settings")
        notebook.add(updates_tab, text="Updates")

        self.settings_ui = SettingsUI(settings_tab, on_theme_change=self._apply_theme_to_text_widgets)
        self._build_run_tab(run_tab)
        self._build_clips_tab(clips_tab)
        self._build_sessions_tab(sessions_tab)
        self._build_updates_tab(updates_tab)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_log_queue()
        self._poll_clip_render_queue()
        self._poll_clip_thumbnail_queue()
        self._poll_game_detection()
        self._check_for_updates()

        # See _apply_theme_to_text_widgets()'s own docstring for why this
        # re-application (not just the colors each Text widget was
        # constructed with) is needed.
        self._apply_theme_to_text_widgets(ui_theme.get_theme())
        if self._first_run:
            self.root.after_idle(self._show_first_run_guide)

    # -----------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------

    def _build_run_tab(self, parent):
        ttk.Label(
            parent,
            text="Save and tag game moments with OBS, or use AI audio highlights if you prefer.",
            wraplength=700,
        ).pack(anchor="w", pady=(0, 12))

        quick_start = ttk.LabelFrame(parent, text="Before you start", padding=10)
        quick_start.pack(fill="x", pady=(0, 12))
        ttk.Label(
            quick_start,
            text=(
                "1. Open OBS and enable its Replay Buffer.\n"
                "2. Choose a game and enter your in-game name above.\n"
                "3. Start capture below. AI audio capture is optional."
            ),
            justify="left",
        ).pack(anchor="w")

        self._build_run_game_options(parent)

        action_row = ttk.Frame(parent)
        action_row.pack(fill="x", pady=(0, 8))

        self.capture_button = ttk.Button(
            action_row,
            text="Start Capturing Highlights",
            command=lambda: self._start_worker("capture"),
            padding=(12, 8),
        )
        self.capture_button.pack(side="left")
        self.capture_button.configure(
            text=self._capture_button_text(self._capture_mode),
        )

        self.stop_button = ttk.Button(
            action_row,
            text="Stop Capture",
            command=self._stop_worker,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=(8, 0))

        self.preflight_button = ttk.Button(
            action_row,
            text="Check setup",
            command=self._start_obs_preflight,
        )
        self.preflight_button.pack(side="left", padx=(8, 0))

        ttk.Button(
            action_row,
            text="Settings",
            command=lambda: self.main_notebook.select(3),
        ).pack(side="right")

        self.status_label = ttk.Label(
            parent,
            text="Ready. Open OBS and enable Replay Buffer, then start capture.",
            font=("TkDefaultFont", 10, "bold"),
        )
        self.status_label.pack(fill="x", pady=(0, 4))

        next_step_row = ttk.Frame(parent)
        next_step_row.pack(fill="x", pady=(0, 10))
        self.next_step_label = ttk.Label(
            next_step_row,
            text="Saved clips will appear in the Clips tab.",
            wraplength=570,
        )
        self.next_step_label.pack(side="left", fill="x", expand=True)
        self.review_clips_button = ttk.Button(
            next_step_row,
            text="Review clips",
            command=self._open_clips_tab,
            state="disabled",
        )
        self.review_clips_button.pack(side="right", padx=(8, 0))

        self.preflight_frame = ttk.LabelFrame(parent, text="Setup checklist", padding=8)
        self.preflight_titles = {
            "obs": "OBS connection",
            "audio": "Audio input (AI mode)",
            "recording": "OBS recording folder",
            "output": "Clip output folder",
            "ocr": "Game-event OCR",
        }
        self.preflight_rows = {}
        for key, title in self.preflight_titles.items():
            label = ttk.Label(
                self.preflight_frame,
                text=f"{title}: Not checked",
                wraplength=700,
            )
            label.pack(anchor="w", pady=1)
            self.preflight_rows[key] = label

        processing = ttk.LabelFrame(parent, text="After capture", padding=8)
        self.processing_frame = processing
        processing.pack(fill="x", pady=(0, 8))
        ttk.Label(
            processing,
            text="Choose what happens to saved clips when capture stops:",
        ).pack(anchor="w", pady=(0, 4))

        self.verify_button = ttk.Button(
            processing,
            text="Prepare clips for review",
            command=lambda: self._start_worker("verify"),
        )
        self.verify_button.pack(side="left")

        self.render_button = ttk.Button(
            processing,
            text="Finish approved clips",
            command=lambda: self._start_worker("render"),
        )
        self.render_button.pack(side="left", padx=(8, 0))

        pipeline_config = app_config.load_config()

        self.auto_verify_var = tk.BooleanVar(value=bool(pipeline_config.get("auto_verify", True)))
        self.auto_render_var = tk.BooleanVar(value=bool(pipeline_config.get("auto_render", True)))

        ttk.Checkbutton(
            processing,
            text="Automatically prepare clips for review after capture",
            variable=self.auto_verify_var,
            command=self._save_pipeline_settings,
        ).pack(anchor="w", pady=(6, 0))

        ttk.Checkbutton(
            processing,
            text="Automatically finish clips after they are approved",
            variable=self.auto_render_var,
            command=self._save_pipeline_settings,
        ).pack(anchor="w")

        ttk.Label(parent, text="Recent activity").pack(anchor="w", pady=(4, 2))
        self.activity_text = tk.Text(
            parent,
            height=5,
            state="disabled",
            wrap="word",
            **self._text_widget_colors,
        )
        self.activity_text.pack(fill="x", pady=(0, 4))
        self._activity_messages = ["No recent activity yet."]
        self._refresh_activity_feed()

        log_header = ttk.Frame(parent)
        log_header.pack(fill="x", pady=(4, 0))
        self.log_toggle_button = ttk.Button(
            log_header,
            text="Show technical details",
            command=self._toggle_activity_log,
        )
        self.log_toggle_button.pack(side="left")

        log_frame = ttk.Frame(parent)
        self.log_text = tk.Text(
            log_frame,
            state="disabled",
            wrap="word",
            **self._text_widget_colors,
        )
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.activity_log_frame = log_frame
        self._activity_log_visible = False

    def _build_run_game_options(self, parent):
        settings = app_config.load_config()
        selected_game = str(settings.get("game_selection", "auto"))
        self._game_choice_to_value = {"Auto-detect": "auto"}
        self._game_value_to_choice = {"auto": "Auto-detect"}
        for profile in game_detector.load_game_profiles():
            game_name = profile["game_name"]
            self._game_choice_to_value[game_name] = game_name
            self._game_value_to_choice[game_name] = game_name
        self._game_choice_to_value["Custom..."] = "custom"
        self._game_value_to_choice["custom"] = "Custom..."

        game_choice = self._game_value_to_choice.get(selected_game, "Auto-detect")
        self.game_selection_var = tk.StringVar(value=game_choice)
        capture_mode = str(settings.get("capture_mode", "game_events"))
        self._capture_mode_to_label = {
            "game_events": "Game events (low resource)",
            "ai": "AI audio and transcript (more resource use)",
        }
        self._capture_label_to_mode = {
            label: mode for mode, label in self._capture_mode_to_label.items()
        }
        self.capture_mode_var = tk.StringVar(
            value=self._capture_mode_to_label.get(capture_mode, self._capture_mode_to_label["game_events"])
        )
        self._capture_mode = capture_mode if capture_mode in self._capture_mode_to_label else "game_events"
        self.game_player_name_var = self.settings_ui.game_player_name_var

        game_frame = ttk.LabelFrame(parent, text="Game highlights (optional)", padding=8)
        game_frame.pack(fill="x", pady=(0, 8))

        mode_row = ttk.Frame(game_frame)
        mode_row.pack(fill="x", pady=(0, 8))
        ttk.Label(mode_row, text="Capture mode:").pack(side="left")
        self.capture_mode_combo = ttk.Combobox(
            mode_row,
            textvariable=self.capture_mode_var,
            values=tuple(self._capture_label_to_mode),
            state="readonly",
            width=40,
        )
        self.capture_mode_combo.pack(side="left", padx=(8, 0))
        self.capture_mode_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._save_run_game_settings(),
        )

        game_row = ttk.Frame(game_frame)
        game_row.pack(fill="x")
        ttk.Label(game_row, text="Game:").pack(side="left")
        self.game_selection_combo = ttk.Combobox(
            game_row,
            textvariable=self.game_selection_var,
            values=tuple(self._game_choice_to_value),
            state="readonly",
            width=28,
        )
        self.game_selection_combo.pack(side="left", padx=(8, 0))
        self.game_selection_combo.bind(
            "<<ComboboxSelected>>",
            self._on_game_selection_changed,
        )
        self.custom_game_name_var = tk.StringVar(
            value=str(settings.get("custom_game_name", ""))
        )
        self.custom_game_process_var = tk.StringVar(
            value=str(settings.get("custom_game_process_name", ""))
        )
        self.custom_game_frame = ttk.Frame(game_frame)
        ttk.Label(self.custom_game_frame, text="Custom game name:").pack(anchor="w")
        self.custom_game_name_entry = ttk.Entry(
            self.custom_game_frame,
            textvariable=self.custom_game_name_var,
        )
        self.custom_game_name_entry.pack(fill="x", pady=(2, 6))
        self.custom_game_name_entry.bind(
            "<FocusOut>",
            lambda _event: self._save_run_game_settings(),
        )
        self.custom_game_name_entry.bind(
            "<Return>",
            lambda _event: self._save_run_game_settings(),
        )
        ttk.Label(
            self.custom_game_frame,
            text="Game process name (usually ends in .exe):",
        ).pack(anchor="w")
        self.custom_game_process_entry = ttk.Entry(
            self.custom_game_frame,
            textvariable=self.custom_game_process_var,
        )
        self.custom_game_process_entry.pack(fill="x", pady=(2, 4))
        self.custom_game_process_entry.bind(
            "<FocusOut>",
            lambda _event: self._save_run_game_settings(),
        )
        self.custom_game_process_entry.bind(
            "<Return>",
            lambda _event: self._save_run_game_settings(),
        )
        ttk.Label(
            self.custom_game_frame,
            text="Find the process name in Task Manager > Details. Custom games use "
            "the same basic on-screen event recognition.",
            wraplength=680,
            foreground="#888888",
        ).pack(anchor="w")
        if selected_game.casefold() == "custom":
            self.custom_game_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(
            game_frame,
            text="Choose Auto-detect if you are not sure which game to select.",
            wraplength=680,
            foreground="#888888",
        ).pack(anchor="w", pady=(4, 0))

        ttk.Label(game_frame, text="Your in-game name (optional):").pack(anchor="w", pady=(8, 2))
        self.game_player_name_entry = ttk.Entry(
            game_frame,
            textvariable=self.game_player_name_var,
        )
        self.game_player_name_entry.pack(fill="x")
        self.game_player_name_entry.bind("<Return>", lambda _event: self._save_run_game_settings())
        self.game_player_name_entry.bind("<FocusOut>", lambda _event: self._save_run_game_settings())
        ttk.Label(
            game_frame,
            text="Used to recognize your kills and deaths from the game's on-screen feed. "
            "Enter the name exactly as it appears in-game. Changes apply to your next capture.",
            wraplength=680,
            foreground="#888888",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Button(
            game_frame,
            text="Save game settings",
            command=self._save_run_game_settings,
        ).pack(anchor="e", pady=(6, 0))

        self.game_status_label = ttk.Label(
            game_frame,
            text="Game events: checking for a supported game...",
            wraplength=680,
        )
        self.game_status_label.pack(anchor="w", pady=(6, 0))

    @staticmethod
    def _capture_button_text(capture_mode):
        if capture_mode == "game_events":
            return "Start Game Highlights"
        return "Start AI Highlights"

    def _on_game_selection_changed(self, _event=None):
        if self._game_choice_to_value.get(self.game_selection_var.get()) == "custom":
            self.custom_game_frame.pack(fill="x", pady=(8, 0))
        else:
            self.custom_game_frame.pack_forget()
        self._save_run_game_settings()

    def _save_run_game_settings(self):
        selected_game = self._game_choice_to_value.get(
            self.game_selection_var.get(),
            "auto",
        )
        config = app_config.load_config()
        config["game_selection"] = selected_game
        config["custom_game_name"] = self.custom_game_name_var.get().strip()
        config["custom_game_process_name"] = self.custom_game_process_var.get().strip()
        config["capture_mode"] = self._capture_label_to_mode.get(
            self.capture_mode_var.get(),
            "game_events",
        )
        self._capture_mode = config["capture_mode"]
        config["game_player_name"] = self.game_player_name_var.get().strip()
        app_config.save_config(config)
        self.game_player_name_var.set(config["game_player_name"])
        self.capture_button.configure(
            text=self._capture_button_text(config["capture_mode"]),
        )
        self._refresh_game_detection()

    def _show_first_run_guide(self):
        guide = tk.Toplevel(self.root)
        guide.title("Welcome to OBS AI Highlights")
        guide.transient(self.root)
        guide.resizable(False, False)

        body = ttk.Frame(guide, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="A few quick steps before your first capture",
            font=("TkDefaultFont", 12, "bold"),
        ).pack(anchor="w", pady=(0, 10))
        ttk.Label(
            body,
            text=(
                "1. Open OBS and enable its WebSocket server in Tools > WebSocket Server Settings.\n"
                "2. Choose a game and enter your in-game name on the Run tab. Configure audio "
                "only if you choose AI audio capture.\n"
                "3. Check setup, then start capturing highlights."
            ),
            justify="left",
            wraplength=520,
        ).pack(anchor="w")

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(16, 0))
        ttk.Button(
            buttons,
            text="Open Settings",
            command=lambda: self._open_settings_from_guide(guide),
        ).pack(side="left")
        ttk.Button(buttons, text="I'll do this later", command=guide.destroy).pack(side="right")

    def _open_settings_from_guide(self, guide):
        guide.destroy()
        self.main_notebook.select(3)

    def _toggle_activity_log(self):
        if self._activity_log_visible:
            self.activity_log_frame.pack_forget()
            self.log_toggle_button.configure(text="Show technical details")
            self._activity_log_visible = False
        else:
            self.activity_log_frame.pack(fill="both", expand=True, pady=(6, 0))
            self.log_toggle_button.configure(text="Hide technical details")
            self._activity_log_visible = True

    def _refresh_activity_feed(self):
        self.activity_text.configure(state="normal")
        self.activity_text.delete("1.0", "end")
        self.activity_text.insert("1.0", "\n".join(self._activity_messages))
        self.activity_text.see("end")
        self.activity_text.configure(state="disabled")

    def _append_activity(self, message):
        if self._activity_messages == ["No recent activity yet."]:
            self._activity_messages.clear()
        self._activity_messages.append(message)
        self._activity_messages = self._activity_messages[-8:]
        self._refresh_activity_feed()

    def _start_obs_preflight(self):
        if self._preflight_check_running:
            return

        if not self.preflight_frame.winfo_manager():
            self.preflight_frame.pack(
                before=self.processing_frame,
                fill="x",
                pady=(0, 8),
            )
        self._preflight_check_running = True
        self.preflight_button.configure(state="disabled")
        for key, label in self.preflight_rows.items():
            label.configure(text=f"{self.preflight_titles[key]}: Checking...")
        self._append_activity("Checking OBS, audio, and folder setup...")
        threading.Thread(target=self._run_obs_preflight, daemon=True).start()

    def _run_obs_preflight(self):
        result = _check_obs_preflight()
        self.log_queue.put(("__PREFLIGHT__", result))

    def _on_obs_preflight_done(self, result):
        self._preflight_check_running = False
        self.preflight_button.configure(state="normal")
        statuses = {
            "ready": "Ready",
            "warning": "Needs attention",
            "optional": "Optional",
        }
        for key, title, level, message, detail in result:
            label = self.preflight_rows[key]
            label.configure(text=f"{title}: {statuses.get(level, 'Status')} — {message}")
            self._append_log(f"Pre-flight - {title}: {message}\n")
            if detail:
                self._append_log(f"Pre-flight details - {title}: {detail}\n")
            if level == "warning":
                self._append_activity(f"Setup needs attention: {message}")
        if not any(check[2] == "warning" for check in result):
            self._append_activity("Setup check finished. No required items need attention.")

    def _save_pipeline_settings(self):
        # Read-modify-write against the current on-disk config rather
        # than any in-memory copy, same reasoning as SettingsUI._save()'s
        # own fresh-read fix - this and the Settings tab both write to
        # the same file independently.
        config = app_config.load_config()
        config["auto_verify"] = self.auto_verify_var.get()
        config["auto_render"] = self.auto_render_var.get()
        app_config.save_config(config)

    def _poll_game_detection(self):
        self._refresh_game_detection()
        self.root.after(5000, self._poll_game_detection)

    def _refresh_game_detection(self):
        try:
            config = app_config.load_config()
            selected_game = config.get("game_selection", "auto")
            custom_profile = {
                "game_name": config.get("custom_game_name", ""),
                "process_name": config.get("custom_game_process_name", ""),
            }
            game = game_detector.detect_running_game(
                selected_game=selected_game if selected_game != "auto" else None,
                custom_profile=custom_profile if selected_game == "custom" else None,
            )
            text = (
                game_detector.format_game_detection(game)
                if game is not None or selected_game == "auto"
                else (
                    "Enter a custom game name and process name above."
                    if selected_game == "custom"
                    and not (
                        custom_profile["game_name"].strip()
                        and custom_profile["process_name"].strip()
                    )
                    else f"{custom_profile['game_name'] or selected_game} is not running yet."
                )
            )
        except Exception as error:
            text = f"Game detection unavailable: {error}"

        self.game_status_label.configure(text=text)

    def _build_sessions_tab(self, parent):
        ttk.Label(
            parent,
            text=(
                "Each Highlight Capture run is a session - browse detection stats to tune "
                "presets against real data instead of guessing."
            ),
            wraplength=680,
        ).pack(anchor="w", pady=(0, 8))

        self.sessions_empty_label = ttk.Label(
            parent,
            text="No capture sessions yet. Start capturing highlights from the Run tab to see session stats here.",
            wraplength=680,
        )

        list_frame = ttk.Frame(parent)
        self.sessions_list_frame = list_frame
        self.sessions_empty_label.pack(anchor="w", pady=(0, 8))
        list_frame.pack(fill="both", expand=True)

        columns = ("label", "started", "thoughts", "saved")
        self.sessions_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=8)
        self.sessions_tree.heading("label", text="Session")
        self.sessions_tree.heading("started", text="Started")
        self.sessions_tree.heading("thoughts", text="Thoughts")
        self.sessions_tree.heading("saved", text="Saved")
        self.sessions_tree.column("label", width=260)
        self.sessions_tree.column("started", width=140)
        self.sessions_tree.column("thoughts", width=80, anchor="center")
        self.sessions_tree.column("saved", width=80, anchor="center")
        self.sessions_tree.pack(side="left", fill="both", expand=True)

        tree_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.sessions_tree.yview)
        self.sessions_tree.configure(yscrollcommand=tree_scrollbar.set)
        tree_scrollbar.pack(side="left", fill="y")

        self.sessions_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_session_selected())

        button_row = ttk.Frame(parent)
        button_row.pack(fill="x", pady=(8, 8))

        ttk.Button(button_row, text="Refresh", command=self._refresh_sessions_list).pack(side="left")
        ttk.Button(button_row, text="Rename...", command=self._rename_selected_session).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(button_row, text="Delete", command=self._delete_selected_session).pack(
            side="left", padx=(6, 0)
        )

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=(0, 8))

        self.session_detail_text = tk.Text(
            parent,
            height=14,
            state="disabled",
            wrap="word",
            **self._text_widget_colors,
        )
        self.session_detail_text.pack(fill="both", expand=True)

        self._session_id_by_iid = {}
        self._refresh_sessions_list()

    def _refresh_sessions_list(self):
        for row in self.sessions_tree.get_children():
            self.sessions_tree.delete(row)

        self._session_id_by_iid = {}

        records = session_stats.list_sessions()
        if records:
            self.sessions_empty_label.pack_forget()
        elif not self.sessions_empty_label.winfo_manager():
            self.sessions_empty_label.pack(
                before=self.sessions_list_frame,
                anchor="w",
                pady=(0, 8),
            )

        for record in records:
            started = record.get("started_at", "")[:16].replace("T", " ")
            iid = self.sessions_tree.insert(
                "",
                "end",
                values=(
                    record.get("label", ""),
                    started,
                    record.get("thoughts_analyzed", 0),
                    record.get("saved_count", 0),
                ),
            )
            self._session_id_by_iid[iid] = record["session_id"]

        self._clear_session_detail()

    def _selected_session_id(self):
        selection = self.sessions_tree.selection()
        if not selection:
            return None
        return self._session_id_by_iid.get(selection[0])

    def _on_session_selected(self):
        session_id = self._selected_session_id()
        if not session_id:
            self._clear_session_detail()
            return

        record = session_stats.get_session(session_id)
        if not record:
            self._clear_session_detail()
            return

        self._show_session_detail(record)

    def _show_session_detail(self, record):
        lines = []
        lines.append(f"Session: {record.get('label', '')}")
        lines.append(f"Preset: {record.get('preset', '')}")
        lines.append(f"Started: {record.get('started_at', '')}")
        lines.append(f"Ended: {record.get('ended_at') or '(still running, or ended abnormally)'}")
        lines.append("")
        lines.append(f"Thoughts analyzed: {record.get('thoughts_analyzed', 0)}")
        lines.append(f"  Ignored:  {record.get('ignored_count', 0)}")
        lines.append(f"  Possible: {record.get('possible_count', 0)}")
        lines.append(f"  Saved:    {record.get('saved_count', 0)}")
        lines.append(f"Average score: {session_stats.average_score(record):.1f}")
        lines.append("")

        lines.append("Phrases most responsible (drove a score up):")
        top = session_stats.top_phrases(record, key="phrase_hit_counts")
        if top:
            for phrase, count in top:
                lines.append(f"  {count:>3}x  {phrase}")
        else:
            lines.append("  (none)")
        lines.append("")

        lines.append("Rejected/admin phrases (dragged a score down):")
        top_admin = session_stats.top_phrases(record, key="admin_phrase_hit_counts")
        if top_admin:
            for phrase, count in top_admin:
                lines.append(f"  {count:>3}x  {phrase}")
        else:
            lines.append("  (none)")
        lines.append("")

        clips = record.get("saved_clips", [])
        lines.append(f"Saved clips ({len(clips)}):")
        for clip in clips:
            lines.append(f"  {clip}")

        self.session_detail_text.configure(state="normal")
        self.session_detail_text.delete("1.0", "end")
        self.session_detail_text.insert("1.0", "\n".join(lines))
        self.session_detail_text.configure(state="disabled")

    def _clear_session_detail(self):
        self.session_detail_text.configure(state="normal")
        self.session_detail_text.delete("1.0", "end")
        self.session_detail_text.configure(state="disabled")

    def _rename_selected_session(self):
        session_id = self._selected_session_id()
        if not session_id:
            return

        record = session_stats.get_session(session_id)
        if not record:
            return

        new_label = simpledialog.askstring(
            "Rename Session", "New name:", initialvalue=record.get("label", ""), parent=self.root
        )
        if not new_label:
            return

        session_stats.rename_session(session_id, new_label.strip())
        self._refresh_sessions_list()

    def _delete_selected_session(self):
        session_id = self._selected_session_id()
        if not session_id:
            return

        record = session_stats.get_session(session_id)
        if not record:
            return

        if not messagebox.askyesno(
            "Delete session",
            f'Delete stats for "{record.get("label", "")}"?\n\n'
            "This only removes the stats record - it does not delete the actual clip files.",
        ):
            return

        session_stats.delete_session(session_id)
        self._refresh_sessions_list()

    def _build_clips_tab(self, parent):
        ttk.Label(
            parent,
            text=(
                "Every detected clip, across every pipeline stage - play it, approve or reject "
                "a Review clip, render a Verified one on demand, or delete it outright. "
                "Ctrl+click or Shift+click to select several at once and act on them together. "
                "Click a column header to sort."
            ),
            wraplength=680,
        ).pack(anchor="w", pady=(0, 8))

        filter_row = ttk.Frame(parent)
        filter_row.pack(fill="x", pady=(0, 6))

        ttk.Label(filter_row, text="Status:").pack(side="left")

        self.clip_status_filter_var = tk.StringVar(value="All")
        self.clip_status_filter_combo = ttk.Combobox(
            filter_row,
            textvariable=self.clip_status_filter_var,
            values=["All"] + CLIP_STATUS_FILTER_VALUES,
            state="readonly",
            width=12,
        )
        self.clip_status_filter_combo.pack(side="left", padx=(8, 0))
        self.clip_status_filter_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._refresh_clips_list()
        )

        self.clips_empty_frame = ttk.Frame(parent, padding=(4, 4))
        self.clips_empty_label = ttk.Label(
            self.clips_empty_frame,
            text="No clips yet. Start capture from the Run tab, then prepare saved clips for review.",
            wraplength=600,
        )
        self.clips_empty_label.pack(side="left", fill="x", expand=True)
        self.clips_empty_button = ttk.Button(
            self.clips_empty_frame,
            text="Go to Run tab",
            command=lambda: self.main_notebook.select(0),
        )
        self.clips_empty_button.pack(side="right", padx=(8, 0))

        list_frame = ttk.Frame(parent)
        self.clips_list_frame = list_frame
        self.clips_empty_frame.pack(fill="x", pady=(0, 6))
        list_frame.pack(fill="both", expand=True)

        columns = ("clip", "score", "status", "duration", "rendered")
        self._clip_column_labels = {
            "clip": "Clip",
            "score": "Score",
            "status": "Status",
            "duration": "Duration",
            "rendered": "Rendered",
        }
        self._clips_sort_key = None
        self._clips_sort_reverse = False

        self.clips_tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", height=8, selectmode="extended"
        )
        for column in columns:
            self.clips_tree.heading(
                column,
                text=self._clip_column_labels[column],
                command=lambda c=column: self._sort_clips_by(c),
            )
        self.clips_tree.column("clip", width=220)
        self.clips_tree.column("score", width=60, anchor="center")
        self.clips_tree.column("status", width=90, anchor="center")
        self.clips_tree.column("duration", width=80, anchor="center")
        self.clips_tree.column("rendered", width=80, anchor="center")
        self.clips_tree.pack(side="left", fill="both", expand=True)

        tree_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.clips_tree.yview)
        self.clips_tree.configure(yscrollcommand=tree_scrollbar.set)
        tree_scrollbar.pack(side="left", fill="y")

        self.clips_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_clip_selected())

        button_row = ttk.Frame(parent)
        button_row.pack(fill="x", pady=(8, 8))

        ttk.Button(button_row, text="Refresh", command=self._refresh_clips_list).pack(side="left")

        self.clip_play_button = ttk.Button(button_row, text="Play", command=self._play_selected_clip)
        self.clip_play_button.pack(side="left", padx=(6, 0))

        self.clip_approve_button = ttk.Button(
            button_row, text="Approve", command=self._approve_selected_clip
        )
        self.clip_approve_button.pack(side="left", padx=(6, 0))

        self.clip_reject_button = ttk.Button(
            button_row, text="Reject", command=self._reject_selected_clip
        )
        self.clip_reject_button.pack(side="left", padx=(6, 0))

        self.clip_render_button = ttk.Button(
            button_row, text="Render", command=self._render_selected_clip
        )
        self.clip_render_button.pack(side="left", padx=(6, 0))

        self.clip_delete_button = ttk.Button(
            button_row, text="Delete", command=self._delete_selected_clip
        )
        self.clip_delete_button.pack(side="left", padx=(6, 0))

        clip_separator = ttk.Separator(parent, orient="horizontal")
        clip_separator.pack(fill="x", pady=(0, 8))

        # Not packed here - shown/hidden around a render via pack()/
        # pack_forget() in _render_selected_clip()/_poll_clip_render_queue().
        # pack(before=clip_separator) keeps it anchored in this slot (just
        # above the separator) every time it's re-shown, rather than
        # jumping to the end of parent's pack order the way a bare
        # pack() would on any call after the tab's initial layout.
        self.clip_render_progress = ttk.Progressbar(parent, mode="indeterminate")
        self._clip_render_progress_anchor = clip_separator

        preview_row = ttk.Frame(parent)
        preview_row.pack(fill="x", pady=(0, 8))

        self.clip_thumbnail_label = ttk.Label(preview_row)
        self.clip_thumbnail_label.pack(side="left", padx=(0, 16))

        trim_frame = ttk.Frame(preview_row)
        trim_frame.pack(side="left", fill="y")

        ttk.Label(trim_frame, text="Trim (seconds):").pack(anchor="w")

        trim_fields_row = ttk.Frame(trim_frame)
        trim_fields_row.pack(anchor="w", pady=(2, 4))

        ttk.Label(trim_fields_row, text="Start:").pack(side="left")
        self.clip_trim_start_var = tk.StringVar()
        ttk.Entry(trim_fields_row, textvariable=self.clip_trim_start_var, width=8).pack(
            side="left", padx=(4, 10)
        )

        ttk.Label(trim_fields_row, text="End:").pack(side="left")
        self.clip_trim_end_var = tk.StringVar()
        ttk.Entry(trim_fields_row, textvariable=self.clip_trim_end_var, width=8).pack(
            side="left", padx=(4, 0)
        )

        self.clip_save_trim_button = ttk.Button(
            trim_frame, text="Save Trim", command=self._save_clip_trim, state="disabled"
        )
        self.clip_save_trim_button.pack(anchor="w")

        ttk.Label(
            trim_frame,
            text="Overrides the auto-detected trim on a Verified/Review clip - re-render to see it.",
            wraplength=360,
            foreground="#888888",
        ).pack(anchor="w", pady=(4, 0))

        self.clip_detail_text = tk.Text(
            parent,
            height=14,
            state="disabled",
            wrap="word",
            **self._text_widget_colors,
        )
        self.clip_detail_text.pack(fill="both", expand=True)

        self._clip_by_iid = {}
        self._current_thumbnail_image = None
        self._clip_thumbnail_request_id = 0
        self._refresh_clips_list()

    def _sort_clips_by(self, column):
        if self._clips_sort_key == column:
            self._clips_sort_reverse = not self._clips_sort_reverse
        else:
            self._clips_sort_key = column
            self._clips_sort_reverse = False

        self._refresh_clips_list()

    def _refresh_clips_list(self):
        for row in self.clips_tree.get_children():
            self.clips_tree.delete(row)

        self._clip_by_iid = {}

        all_clips = clip_manager.list_clips()
        self._all_clips_count = len(all_clips)
        self.review_clips_button.configure(
            state="normal" if all_clips else "disabled"
        )
        clips = all_clips

        status_filter = self.clip_status_filter_var.get()
        if status_filter != "All":
            clips = [clip for clip in clips if clip["status"] == status_filter]

        if not clips:
            if all_clips:
                self.clips_empty_label.configure(
                    text="No clips match this status. Change the filter to All to see your clips."
                )
                self.clips_empty_button.configure(
                    text="Show all clips",
                    command=self._show_all_clips,
                )
            else:
                self.clips_empty_label.configure(
                    text=(
                        "No clips yet. Start capture from the Run tab, then prepare saved "
                        "clips for review."
                    )
                )
                self.clips_empty_button.configure(
                    text="Go to Run tab",
                    command=lambda: self.main_notebook.select(0),
                )
            if not self.clips_empty_frame.winfo_manager():
                self.clips_empty_frame.pack(
                    before=self.clips_list_frame,
                    fill="x",
                    pady=(0, 6),
                )
        else:
            self.clips_empty_frame.pack_forget()

        if self._clips_sort_key:
            clips.sort(
                key=CLIP_SORT_KEY_FUNCS[self._clips_sort_key],
                reverse=self._clips_sort_reverse,
            )

        for column, label in self._clip_column_labels.items():
            indicator = ""
            if column == self._clips_sort_key:
                indicator = " ▼" if self._clips_sort_reverse else " ▲"
            self.clips_tree.heading(column, text=label + indicator)

        for clip in clips:
            iid = self.clips_tree.insert(
                "",
                "end",
                values=(
                    clip_manager.format_clip_label(clip["base_name"]),
                    clip["score"] if clip["score"] is not None else "",
                    clip["status"],
                    f"{clip['duration']:.1f}s" if clip["duration"] is not None else "",
                    "Yes" if clip["rendered"] else "No",
                ),
            )
            self._clip_by_iid[iid] = clip["base_name"]

        self._clear_clip_detail()

    def _show_all_clips(self):
        self.clip_status_filter_var.set("All")
        self._refresh_clips_list()

    def _open_clips_tab(self):
        self.main_notebook.select(1)
        self._refresh_clips_list()

    def _selected_clip_base_names(self):
        return [
            self._clip_by_iid[iid] for iid in self.clips_tree.selection() if iid in self._clip_by_iid
        ]

    def _on_clip_selected(self):
        base_names = self._selected_clip_base_names()

        if not base_names:
            self._clear_clip_detail()
            return

        if len(base_names) == 1:
            clip = clip_manager.get_clip(base_names[0])
            self._show_clip_detail(clip)
            self._load_clip_trim_fields(clip)
            self._load_clip_thumbnail_async(base_names[0])
            return

        self.clip_detail_text.configure(state="normal")
        self.clip_detail_text.delete("1.0", "end")
        self.clip_detail_text.insert("1.0", f"{len(base_names)} clips selected.")
        self.clip_detail_text.configure(state="disabled")
        self._clear_clip_trim_fields()
        self._clear_clip_thumbnail()

    def _show_clip_detail(self, clip):
        lines = []
        lines.append(f"Clip: {clip_manager.format_clip_label(clip['base_name'])}")
        lines.append(f"Status: {clip['status']}")
        lines.append(f"Score: {clip['score'] if clip['score'] is not None else '(unknown)'}")
        duration = clip["duration"]
        lines.append(f"Duration: {f'{duration:.1f}s' if duration is not None else '(unknown)'}")
        lines.append(f"Rendered: {'Yes' if clip['rendered'] else 'No'}")
        lines.append("")

        game_context = clip.get("game_context")
        if game_context:
            lines.append(f"Game: {game_context.get('game', '(unknown)')}")
            lines.append(f"Game profile: {game_context.get('profile', '(unknown)')}")
            lines.append(f"Game score: {game_context.get('score', 0)}")
            tags = game_context.get("tags", [])
            lines.append(f"Tags: {', '.join(tags) if tags else '(none recorded)'}")
            lines.append("Achievements:")
            achievements = game_context.get("achievements", [])
            if achievements:
                lines.extend(f"  - {achievement}" for achievement in achievements)
            else:
                lines.append("  (none recorded)")
            lines.append("Game events:")
            events = game_context.get("events", [])
            if events:
                lines.extend(
                    f"  - {event.get('type', 'EVENT')}: {event.get('text', '')}"
                    for event in events[-12:]
                )
            else:
                lines.append("  (none recorded)")
            lines.append("")

        lines.append("Detection reasons:")
        if clip["reasons"]:
            for reason in clip["reasons"]:
                lines.append(f"  - {reason}")
        else:
            lines.append("  (none recorded)")
        lines.append("")

        lines.append("Transcript:")
        lines.append(clip["transcript"] or "(none)")

        self.clip_detail_text.configure(state="normal")
        self.clip_detail_text.delete("1.0", "end")
        self.clip_detail_text.insert("1.0", "\n".join(lines))
        self.clip_detail_text.configure(state="disabled")

    def _clear_clip_detail(self):
        self.clip_detail_text.configure(state="normal")
        self.clip_detail_text.delete("1.0", "end")
        self.clip_detail_text.configure(state="disabled")
        self._clear_clip_trim_fields()
        self._clear_clip_thumbnail()

    def _load_clip_trim_fields(self, clip):
        trim_start = clip.get("trim_start")
        trim_end = clip.get("trim_end")

        if trim_start is None or trim_end is None:
            self._clear_clip_trim_fields()
            return

        self.clip_trim_start_var.set(f"{trim_start:.2f}")
        self.clip_trim_end_var.set(f"{trim_end:.2f}")
        self.clip_save_trim_button.configure(state="normal")

    def _clear_clip_trim_fields(self):
        self.clip_trim_start_var.set("")
        self.clip_trim_end_var.set("")
        self.clip_save_trim_button.configure(state="disabled")

    def _save_clip_trim(self):
        base_names = self._selected_clip_base_names()
        if len(base_names) != 1:
            return

        base_name = base_names[0]

        ok, message = clip_manager.update_trim(
            base_name, self.clip_trim_start_var.get(), self.clip_trim_end_var.get()
        )

        if not ok:
            messagebox.showerror("Save Trim", message)
            return

        self._refresh_clips_list()

        # selection_set() alone isn't enough here - <<TreeviewSelect>> can
        # fire on a later pass through the event loop rather than inline,
        # so the just-rebuilt row would briefly show as selected with a
        # stale (cleared) detail panel. Calling the handler directly
        # keeps this deterministic regardless of that timing.
        for iid, base in self._clip_by_iid.items():
            if base == base_name:
                self.clips_tree.selection_set(iid)
                self._on_clip_selected()
                break

    def _load_clip_thumbnail_async(self, base_name):
        self._clip_thumbnail_request_id += 1
        request_id = self._clip_thumbnail_request_id

        self.clip_thumbnail_label.configure(image="")
        self._current_thumbnail_image = None

        threading.Thread(
            target=self._clip_thumbnail_worker, args=(base_name, request_id), daemon=True
        ).start()

    def _clip_thumbnail_worker(self, base_name, request_id):
        try:
            thumbnail_path = clip_manager.get_thumbnail(base_name)
        except Exception:
            thumbnail_path = None

        self.clip_thumbnail_queue.put((request_id, thumbnail_path))

    def _poll_clip_thumbnail_queue(self):
        try:
            while True:
                request_id, thumbnail_path = self.clip_thumbnail_queue.get_nowait()

                # Stale result from a clip the user already clicked away
                # from while this was generating - discard it rather
                # than showing the wrong thumbnail under the wrong clip.
                if request_id != self._clip_thumbnail_request_id:
                    continue

                if thumbnail_path:
                    try:
                        image = tk.PhotoImage(file=str(thumbnail_path))
                        self._current_thumbnail_image = image
                        self.clip_thumbnail_label.configure(image=image)
                        continue
                    except Exception:
                        pass

                self.clip_thumbnail_label.configure(image="")
                self._current_thumbnail_image = None
        except queue.Empty:
            pass

        self.root.after(150, self._poll_clip_thumbnail_queue)

    def _clear_clip_thumbnail(self):
        self._clip_thumbnail_request_id += 1
        self.clip_thumbnail_label.configure(image="")
        self._current_thumbnail_image = None

    def _play_selected_clip(self):
        base_names = self._selected_clip_base_names()
        if not base_names:
            return

        if len(base_names) > 1:
            messagebox.showinfo("Play", "Select exactly one clip to play.")
            return

        ok, message = clip_manager.play_clip(base_names[0])
        if not ok:
            messagebox.showerror("Play", message)

    def _report_bulk_result(self, action_label, results):
        """A single clip keeps the old, silent-on-success behavior (only
        an error dialog on failure) - multi-select bulk actions always
        show a short summary, since silently skipping some of several
        selected clips (e.g. approving 3 Review clips when one is
        already Verified) would otherwise be invisible."""
        failed = [result for result in results if not result[1]]

        if len(results) == 1:
            if failed:
                messagebox.showerror(action_label, failed[0][2])
            return

        succeeded_count = len(results) - len(failed)
        lines = [f"{action_label}: {succeeded_count} of {len(results)} clip(s) succeeded."]

        if failed:
            lines.append("")
            lines.append("Not applied to:")
            for base_name, _ok, message in failed[:8]:
                lines.append(f"  - {clip_manager.format_clip_label(base_name)}: {message}")
            if len(failed) > 8:
                lines.append(f"  ...and {len(failed) - 8} more.")

            messagebox.showwarning(action_label, "\n".join(lines))
        else:
            messagebox.showinfo(action_label, "\n".join(lines))

    def _bulk_clip_action(self, action_label, action_fn):
        base_names = self._selected_clip_base_names()
        if not base_names:
            return

        results = [(base_name, *action_fn(base_name)) for base_name in base_names]

        self._report_bulk_result(action_label, results)
        self._refresh_clips_list()

    def _approve_selected_clip(self):
        self._bulk_clip_action("Approve", clip_manager.approve_clip)

    def _reject_selected_clip(self):
        self._bulk_clip_action("Reject", clip_manager.reject_clip)

    def _render_selected_clip(self):
        base_names = self._selected_clip_base_names()
        if not base_names:
            return

        self.clip_approve_button.configure(state="disabled")
        self.clip_reject_button.configure(state="disabled")
        self.clip_render_button.configure(state="disabled", text="Rendering...")
        self.clip_delete_button.configure(state="disabled")

        self.clip_render_progress.pack(
            fill="x", pady=(0, 8), before=self._clip_render_progress_anchor
        )
        self.clip_render_progress.start(12)

        threading.Thread(target=self._render_clips_worker, args=(base_names,), daemon=True).start()

    def _render_clips_worker(self, base_names):
        results = []

        for index, base_name in enumerate(base_names, start=1):
            if len(base_names) > 1:
                self.clip_render_queue.put(("progress", index, len(base_names)))

            try:
                ok, message = clip_manager.render_clip(base_name)
            except Exception as error:
                ok, message = False, f"Unexpected error: {error}"

            results.append((base_name, ok, message))

        self.clip_render_queue.put(("done", results))

    def _poll_clip_render_queue(self):
        try:
            while True:
                item = self.clip_render_queue.get_nowait()

                if item[0] == "progress":
                    _tag, index, total = item
                    self.clip_render_button.configure(text=f"Rendering {index}/{total}...")
                    continue

                _tag, results = item

                self.clip_approve_button.configure(state="normal")
                self.clip_reject_button.configure(state="normal")
                self.clip_render_button.configure(state="normal", text="Render")
                self.clip_delete_button.configure(state="normal")

                self.clip_render_progress.stop()
                self.clip_render_progress.pack_forget()

                self._report_bulk_result("Render", results)
                self._notify_encoder_fallback_once()
                self._refresh_clips_list()
        except queue.Empty:
            pass

        self.root.after(100, self._poll_clip_render_queue)

    def _apply_theme_to_text_widgets(self, theme):
        """sv_ttk retheming every ttk widget happens on its own the moment
        SettingsUI calls ui_theme.apply_theme() - this only has to catch
        the few plain tk.Text widgets sv_ttk can't reach (see
        ui_theme.py's own docstring).

        sv_ttk's own <<ThemeChanged>> handling (tk_setPalette, in its
        bundled sv.tcl) also walks and recolors every existing plain-tk
        widget - including these same Text widgets, clobbering whatever
        was just set here - but via a queued event, not synchronously,
        so exactly when that lands relative to this call isn't reliable
        to depend on. Applying now (best-effort, avoids a flash of the
        wrong colors) and again via after_idle (once every already-
        queued Tk event, that clobbering included, has drained)
        guarantees this is the last word regardless of that timing."""
        self._text_widget_colors = ui_theme.text_widget_colors(theme)

        def _apply():
            for widget in (
                self.activity_text,
                self.log_text,
                self.session_detail_text,
                self.clip_detail_text,
            ):
                widget.configure(**self._text_widget_colors)

        _apply()
        self.root.after_idle(_apply)

    def _notify_encoder_fallback_once(self):
        """A render triggered from the Clips tab runs in-process (no
        console for a --windowed build to print render_clips.py's own
        fallback warning to), so without this, NVENC silently failing
        and falling back to CPU would just look like an unexplained
        slowdown. Shown at most once per app run, not every render."""
        if self._encoder_fallback_notice_shown:
            return

        if not clip_manager.encoder_fallback_active():
            return

        self._encoder_fallback_notice_shown = True

        messagebox.showinfo(
            "Render encoder",
            "NVENC isn't available on this machine (no NVIDIA GPU, or the driver is too "
            "old), so rendering is using CPU/software encoding instead - it'll work, just "
            "slower. Switch 'Render encoder' to CPU / Software on the Settings tab's Video "
            "Style page to skip this check on future renders.",
        )

    def _delete_selected_clip(self):
        base_names = self._selected_clip_base_names()
        if not base_names:
            return

        if len(base_names) == 1:
            prompt = (
                f"Permanently delete \"{clip_manager.format_clip_label(base_names[0])}\"?\n\n"
                "This removes the raw video, its transcript, any Verified/Review record, "
                "and a rendered Short if one exists. This cannot be undone."
            )
        else:
            prompt = (
                f"Permanently delete {len(base_names)} clips?\n\n"
                "This removes each clip's raw video, transcript, any Verified/Review record, "
                "and a rendered Short if one exists. This cannot be undone."
            )

        if not messagebox.askyesno("Delete clip" if len(base_names) == 1 else "Delete clips", prompt):
            return

        self._bulk_clip_action("Delete", clip_manager.delete_clip)

    def _build_updates_tab(self, parent):
        ttk.Label(
            parent,
            text=f"Current version: v{APP_VERSION}",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        self.update_status_label = ttk.Label(parent, text="")
        self.update_status_label.pack(anchor="w", pady=(0, 10))

        button_row = ttk.Frame(parent)
        button_row.pack(anchor="w")

        self.check_updates_button = ttk.Button(
            button_row,
            text="Check for Updates",
            command=self._check_for_updates,
        )
        self.check_updates_button.pack(side="left")

        self.update_now_button = ttk.Button(
            button_row,
            text="Update Now",
            command=self._start_update,
            state="disabled",
        )
        self.update_now_button.pack(side="left", padx=(8, 0))

        self.view_release_button = ttk.Button(
            button_row,
            text="View Release Notes",
            command=self._open_latest_release,
            state="disabled",
        )
        self.view_release_button.pack(side="left", padx=(8, 0))

        self.download_progress_label = ttk.Label(parent, text="")
        self.download_progress_label.pack(anchor="w", pady=(10, 0))

    # -----------------------------------------------------------
    # Update checking
    # -----------------------------------------------------------

    def _check_for_updates(self):
        self.check_updates_button.configure(state="disabled")
        self.update_now_button.configure(state="disabled")
        self.view_release_button.configure(state="disabled")
        self.update_status_label.configure(text="Checking...")
        self.download_progress_label.configure(text="")
        self._latest_release_url = None
        self._latest_asset_url = None
        self._latest_asset_size = None

        threading.Thread(target=self._check_for_updates_worker, daemon=True).start()
        self.root.after(100, self._poll_update_queue)

    def _check_for_updates_worker(self):
        # Push the result onto a thread-safe queue instead of calling
        # self.root.after() from this background thread directly - Tk
        # requires after() to be scheduled from the thread already
        # running the event loop, same reasoning as the log-queue
        # pattern used for worker subprocess output below.
        try:
            tag_name, html_url, asset_url, asset_size = _fetch_latest_release()
            self.update_queue.put((tag_name, html_url, asset_url, asset_size, None))
        except Exception as exc:
            self.update_queue.put((None, None, None, None, exc))

    def _poll_update_queue(self):
        try:
            tag_name, html_url, asset_url, asset_size, error = self.update_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_update_queue)
            return

        self._on_update_check_done(tag_name, html_url, asset_url, asset_size, error)

    def _on_update_check_done(self, tag_name, html_url, asset_url, asset_size, error):
        self.check_updates_button.configure(state="normal")

        if error is not None:
            self.update_status_label.configure(text=f"Couldn't check for updates: {error}")
            return

        if _parse_version(tag_name) > _parse_version(APP_VERSION):
            self.update_status_label.configure(text=f"Update available: {tag_name}")
            self._latest_release_url = html_url
            self._latest_asset_url = asset_url
            self._latest_asset_size = asset_size
            self.view_release_button.configure(state="normal")

            if asset_url:
                self.update_now_button.configure(state="normal")
            else:
                self.download_progress_label.configure(
                    text="No update package or installer found on that release - "
                    "use View Release Notes instead."
                )
        else:
            self.update_status_label.configure(text="You're up to date.")

    def _open_latest_release(self):
        if self._latest_release_url:
            webbrowser.open(self._latest_release_url)

    # -----------------------------------------------------------
    # Update download + install
    # -----------------------------------------------------------

    def _start_update(self):
        if not self._latest_asset_url:
            return

        if self.active_process is not None:
            self.download_progress_label.configure(
                text="Stop the current operation on the Run tab before updating."
            )
            return

        self.check_updates_button.configure(state="disabled")
        self.update_now_button.configure(state="disabled")
        self.download_progress_label.configure(text="Downloading update...")

        threading.Thread(target=self._download_update_worker, daemon=True).start()
        self.root.after(100, self._poll_download_queue)

    def _download_update_worker(self):
        try:
            def report_progress(downloaded, total):
                self.download_queue.put(("progress", downloaded, total))

            installer_path = _download_installer(
                self._latest_asset_url,
                self._latest_asset_size,
                report_progress,
            )
            self.download_queue.put(("done", installer_path, None))
        except Exception as exc:
            self.download_queue.put(("error", None, exc))

    def _poll_download_queue(self):
        try:
            kind, payload, extra = self.download_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_download_queue)
            return

        if kind == "progress":
            downloaded, total = payload, extra
            downloaded_mb = downloaded / (1024 * 1024)
            if total:
                percent = int(downloaded * 100 / total)
                total_mb = total / (1024 * 1024)
                text = f"Downloading update... {percent}% ({downloaded_mb:.1f} MB / {total_mb:.1f} MB)"
            else:
                text = f"Downloading update... {downloaded_mb:.1f} MB"
            self.download_progress_label.configure(text=text)
            self.root.after(100, self._poll_download_queue)
            return

        if kind == "done":
            installer_path = payload
            self.download_progress_label.configure(
                text="Installing update - OBS AI Highlights will restart automatically..."
            )
            self._launch_installer_and_exit(installer_path)
            return

        error = extra
        self.download_progress_label.configure(text=f"Update failed: {error}")
        self.check_updates_button.configure(state="normal")
        self.update_now_button.configure(state="normal")

    def _launch_installer_and_exit(self, installer_path):
        # /SILENT skips every wizard page (no clicking through) but still
        # shows a small progress window, rather than /VERYSILENT's
        # complete silence - some visible sign of life while it installs
        # beats the app just vanishing for a few seconds with no
        # explanation. The installer's own [Run] entry (installer.iss,
        # no "skipifsilent") relaunches the app once it's done, so
        # nothing else here has to wait for or track that.
        try:
            subprocess.Popen([str(installer_path), "/SILENT", "/SUPPRESSMSGBOX", "/NORESTART"])
        except Exception as exc:
            self.download_progress_label.configure(text=f"Couldn't launch installer: {exc}")
            self.check_updates_button.configure(state="normal")
            self.update_now_button.configure(state="normal")
            return

        # The installer's own CloseApplications=yes will also handle this
        # if this races, but closing proactively gives it a clean run at
        # replacing our files instead of fighting a still-open exe.
        self.root.after(500, self.root.destroy)

    # -----------------------------------------------------------
    # Worker process management
    # -----------------------------------------------------------

    def _start_worker(self, role):
        if self.active_process is not None:
            return

        self._append_log(f"--- Starting {WORKER_LABELS[role]} ---\n")
        self._append_activity(f"{WORKER_LABELS[role]} started.")
        status_text = {
            "capture": "Capture is running. OBS AI Highlights is listening for moments to save.",
            "verify": "Preparing saved clips for review...",
            "render": "Finishing approved clips...",
        }
        self.status_label.configure(text=status_text[role])
        if role == "capture":
            self.next_step_label.configure(
                text=(
                    "Keep OBS active. If you started recording or streaming, capture can try "
                    "to start Replay Buffer automatically. Select Stop Capture when you're done."
                )
            )
        elif role == "verify":
            self.next_step_label.configure(
                text="This can take a little while. You can review the prepared clips in the Clips tab."
            )
        else:
            self.next_step_label.configure(
                text="This can take a little while. Finished clips will appear in your output folder."
            )

        command = app_config.worker_launch_command(role)

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                creationflags=creationflags,
            )
        except Exception as exc:
            self._append_log(f"--- Failed to start {WORKER_LABELS[role]}: {exc} ---\n")
            self.status_label.configure(text="Couldn't start that task.")
            self.next_step_label.configure(text="Show technical details below for the error.")
            if not self._activity_log_visible:
                self._toggle_activity_log()
            return

        self.active_process = process
        self.active_role = role

        self.capture_button.configure(state="disabled")
        self.verify_button.configure(state="disabled")
        self.render_button.configure(state="disabled")
        self.stop_button.configure(state="normal")

        threading.Thread(target=self._read_process_output, args=(process,), daemon=True).start()

    def _read_process_output(self, process):
        try:
            for line in process.stdout:
                self.log_queue.put(line)
        finally:
            return_code = process.wait()
            self.log_queue.put(("__DONE__", return_code))

    def _poll_log_queue(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "__DONE__":
                    self._on_worker_done(item[1])
                elif isinstance(item, tuple) and item[0] == "__PREFLIGHT__":
                    self._on_obs_preflight_done(item[1])
                else:
                    self._append_log(item)
        except queue.Empty:
            pass

        self.root.after(100, self._poll_log_queue)

    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

        message = _friendly_activity_message(text)
        if message:
            self._append_activity(message)

    def _on_worker_done(self, return_code):
        role = self.active_role

        self._append_log(f"--- {WORKER_LABELS.get(role, role)} finished (exit code {return_code}) ---\n")
        if role in {"capture", "verify", "render"}:
            self._refresh_clips_list()

        if return_code == 0:
            self._append_activity(f"{WORKER_LABELS.get(role, 'Task')} finished.")
            status_text = {
                "capture": "Capture finished.",
                "verify": "Clip preparation finished.",
                "render": "Clip rendering finished.",
            }
            self.status_label.configure(text=status_text.get(role, "Task finished."))
            if role == "verify" and not self._all_clips_count:
                self.next_step_label.configure(
                    text=(
                        "No clips were found to prepare. Check OBS and audio in the setup "
                        "checklist, then try capturing again."
                    )
                )
            elif role == "verify":
                self.next_step_label.configure(
                    text="Clip preparation is complete. Open Clips to review what was found."
                )
            else:
                self.next_step_label.configure(
                    text="Open Clips to review your saved clips and see what's ready."
                )
        else:
            self._append_activity(
                f"Task ended with an error (exit code {return_code}). "
                "Open technical details for more information."
            )
            self.status_label.configure(text="That task ended with an error.")
            self.next_step_label.configure(
                text="Show technical details below, then check your OBS and folder settings."
            )
            if not self._activity_log_visible:
                self._toggle_activity_log()

        self.active_process = None
        self.active_role = None

        self.capture_button.configure(state="normal")
        self.verify_button.configure(state="normal")
        self.render_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _stop_worker(self):
        if self.active_process is None:
            return

        self._append_log("--- Stop requested ---\n")
        self._append_activity("Stopping the current task...")
        self._kill_process_tree(self.active_process.pid)

    def _kill_process_tree(self, pid):
        """Kills a worker and everything it spawned.

        highlight_engine.py's own post-service auto-chaining means a
        "capture" worker can have a live grandchild (verify/render)
        subprocess running under it - plain terminate() only kills the
        direct child on Windows, leaving the grandchild orphaned and
        still holding GPU memory. psutil walks the whole tree instead,
        same approach already proven safe in the Sunday Service System's
        own process-check fixes."""
        if psutil is None:
            try:
                self.active_process.terminate()
            except Exception:
                pass
            return

        try:
            parent = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return

        children = parent.children(recursive=True)

        for proc in children + [parent]:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass

    def _on_close(self):
        if self.active_process is not None:
            self._kill_process_tree(self.active_process.pid)

        self.root.destroy()


def run_gui():
    root = tk.Tk()

    try:
        root.iconbitmap(str(app_config.resource_path("icon.ico")))
    except Exception:
        pass

    ui_theme.apply_theme(ui_theme.get_theme())

    MainApp(root)

    root.mainloop()


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--worker":
        run_worker(sys.argv[2])
        return

    run_gui()


if __name__ == "__main__":
    main()
