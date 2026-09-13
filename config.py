# -*- coding: utf-8 -*-
"""Config load/save for the standalone highlight-clip tool.

Follows the same shape as SermonAI's core/ptz_settings.py: a dedicated
small JSON file (not mixed into any other project's config), plain
load/save functions, atomic writes. This tool has no dependency on the
SermonAI/SSS codebase at all - it's a fully independent companion app.

The OBS password stays out of highlight_config.json entirely (same
convention the original sermon_ai.py used) - it's read from the
OBS_PASSWORD environment variable / a local .env file, so it never gets
committed to git by accident.
"""

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent


def _app_data_dir():
    """Per-user, always-writable settings folder - not next to the exe.

    An app installed to Program Files can't write there without admin
    rights, so highlight_config.json/.env live under %APPDATA% instead.
    Falls back to the repo folder itself only if APPDATA is unset
    (never true on real Windows, but keeps this importable elsewhere)."""
    appdata = os.getenv("APPDATA")
    base = Path(appdata) / "OBS AI Highlights" if appdata else REPO_ROOT
    base.mkdir(parents=True, exist_ok=True)
    return base


BASE = _app_data_dir()
CONFIG_FILE = BASE / "highlight_config.json"
ENV_FILE = BASE / ".env"

try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
except Exception:
    pass

DEFAULTS = {
    "preset": "church",
    "obs_host": "127.0.0.1",
    "obs_port": 4455,
    "recording_folder": r"D:\2026",
    "output_folder": "",
    "full_transcript_srt_folder": "",
    "audio_device_name": "",
    "audio_device_fallback_index": 0,
    "whisper_model": "small",
    "whisper_device": "cuda",
    "whisper_compute_type": "float16",
    "verify_model": "medium",
    "verify_device": "cuda",
    "verify_compute_type": "int8_float16",
    "possible_threshold": 55,
    "save_threshold": 70,
    "verified_similarity": 82,
    "verified_confidence": 0.70,
    "custom_preset": {},
}


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
    default = {} if default is None else default

    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def load_config():
    """Returns the config dict, DEFAULTS overlaid with whatever's saved."""
    config = dict(DEFAULTS)
    config.update(_read_json(CONFIG_FILE, {}))
    return config


def save_config(config):
    _atomic_write_json(CONFIG_FILE, config)


def get_obs_password():
    """OBS password lives in the environment / .env, never in the JSON
    config file, so it's never accidentally committed or shared."""
    return os.getenv("OBS_PASSWORD", "")


def set_obs_password(password):
    """Writes OBS_PASSWORD into the shared .env file, so the Settings UI
    can offer a normal password field instead of requiring anyone to
    hand-edit a text file. Preserves any other lines already in .env."""
    lines = []
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip().startswith("OBS_PASSWORD="):
                lines.append(line)

    lines.append(f"OBS_PASSWORD={password}")

    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    os.environ["OBS_PASSWORD"] = password


def worker_launch_command(role):
    """[executable, *args] to relaunch this tool as a given worker role
    ("capture" | "verify" | "render") - whether running from source or
    as a frozen PyInstaller build.

    Frozen mode re-invokes the single packaged exe with --worker instead
    of assuming sys.executable can run a sibling .py file - it can't
    once frozen, since sys.executable IS the packaged exe by then."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker", role]

    return [sys.executable, str(REPO_ROOT / "app.py"), "--worker", role]


def get_output_folder(config):
    output = str(config.get("output_folder", "")).strip()

    if output:
        return Path(output)

    return Path(config.get("recording_folder", DEFAULTS["recording_folder"])) / "shorts" / "ai shorts"


def get_full_transcript_srt_folder(config):
    """Optional folder of whole-session SRT files used to cross-check a
    clip's trim against the full recording's own transcript for extra
    accuracy. Blank by default (the cross-check is skipped entirely when
    the folder doesn't exist) since not every preset/user has this."""
    return str(config.get("full_transcript_srt_folder", "")).strip()
