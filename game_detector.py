# -*- coding: utf-8 -*-
"""Detect supported games from their running process and load game profiles."""

import json
import sys
from pathlib import Path

import psutil


def _profile_directory():
    """Resolve bundled profiles both from source and a frozen PyInstaller app."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "profiles"

    return Path(__file__).resolve().parent / "profiles"


def load_game_profiles(profile_dir=None):
    """Load and validate all JSON game profiles in the profile directory."""
    directory = Path(profile_dir) if profile_dir is not None else _profile_directory()
    profiles = []

    for path in sorted(directory.glob("*.json")):
        profile = json.loads(path.read_text(encoding="utf-8"))

        for field in ("game_name", "profile_name", "process_names"):
            if field not in profile:
                raise ValueError(f"{path} is missing required field {field!r}.")

        if (
            not isinstance(profile["process_names"], list)
            or not profile["process_names"]
            or not all(isinstance(name, str) and name.strip() for name in profile["process_names"])
        ):
            raise ValueError(f"{path} must define at least one process name.")

        command_line_checks = profile.get("process_command_line_contains", {})
        if not isinstance(command_line_checks, dict) or any(
            not isinstance(name, str)
            or not isinstance(terms, list)
            or not all(isinstance(term, str) and term for term in terms)
            for name, terms in command_line_checks.items()
        ):
            raise ValueError(
                f"{path} process_command_line_contains must map process names to text lists."
            )

        event_patterns = profile.get("event_patterns", {})
        if not isinstance(event_patterns, dict) or any(
            not isinstance(patterns, list)
            or not all(isinstance(pattern, str) for pattern in patterns)
            for patterns in event_patterns.values()
        ):
            raise ValueError(f"{path} event_patterns must map event types to regex lists.")

        region = profile.get("ocr_region")
        if region is not None and (
            not isinstance(region, dict)
            or any(key not in region for key in ("left", "top", "width", "height"))
        ):
            raise ValueError(f"{path} ocr_region must define left, top, width, and height.")

        profiles.append(profile)

    return profiles


def detect_running_game(
    process_iter=None,
    profile_dir=None,
    selected_game=None,
    custom_profile=None,
    profile_overrides=None,
):
    """Return the matching game/profile and process name, or None if unsupported."""
    profiles = load_game_profiles(profile_dir)
    if selected_game and selected_game.casefold() == "custom":
        game_name = str((custom_profile or {}).get("game_name", "")).strip()
        process_names = (custom_profile or {}).get("process_names")
        if not process_names:
            process_names = str((custom_profile or {}).get("process_name", "")).split(",")
        elif isinstance(process_names, str):
            process_names = process_names.split(",")
        process_names = [
            str(name).strip()
            for name in process_names
            if str(name).strip()
        ]
        profiles = (
            [{
                "game_name": game_name,
                "profile_name": "Custom",
                "process_names": process_names,
                "event_patterns": {},
            }]
            if game_name and process_names
            else []
        )
    elif selected_game and selected_game.casefold() != "auto":
        profiles = [
            profile
            for profile in profiles
            if profile["game_name"].casefold() == selected_game.casefold()
        ]
    profile_overrides = profile_overrides if isinstance(profile_overrides, dict) else {}
    for profile in profiles:
        override = profile_overrides.get(profile["game_name"], {})
        if not isinstance(override, dict):
            continue
        process_names = override.get("process_names")
        if isinstance(process_names, (list, str)):
            if isinstance(process_names, str):
                process_names = process_names.split(",")
            normalized_names = [
                str(name).strip()
                for name in process_names
                if str(name).strip()
            ]
            if normalized_names:
                profile["process_names"] = normalized_names
    processes = process_iter if process_iter is not None else psutil.process_iter(["name", "cmdline"])

    processes_by_name = {}
    for process in processes:
        try:
            process_info = process.info
            name = process_info.get("name")
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

        if name:
            processes_by_name.setdefault(name.casefold(), []).append(process_info)

    for profile in profiles:
        for expected_name in profile["process_names"]:
            candidates = processes_by_name.get(expected_name.casefold(), [])
            command_line_checks = profile.get("process_command_line_contains", {})
            required_terms = (
                command_line_checks.get(expected_name.casefold(), [])
                if isinstance(command_line_checks, dict)
                else []
            )
            for candidate in candidates:
                command_line = " ".join(
                    str(argument) for argument in (candidate.get("cmdline") or [])
                ).casefold()
                if required_terms and not any(
                    str(term).casefold() in command_line
                    for term in required_terms
                ):
                    continue
                return {
                    "game_name": profile["game_name"],
                    "profile_name": profile["profile_name"],
                    "process_name": candidate.get("name"),
                    "profile": profile,
                }

    return None


def format_game_detection(game):
    """Format a detection result for the app's status display."""
    if game is None:
        return "No supported game detected."

    return f"Detected: {game['game_name']}  |  Profile: {game['profile_name']}"
