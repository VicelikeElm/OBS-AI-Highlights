# -*- coding: utf-8 -*-
"""Screen-region OCR and game-event extraction for supported game profiles."""

import hashlib
import re
import threading
import time
from datetime import datetime


DEFAULT_OCR_REGION = {
    "left": 0.70,
    "top": 0.04,
    "width": 0.29,
    "height": 0.30,
}

DEFAULT_EVENT_PATTERNS = {
    "KILL": [],
    "DEATH": [],
    "PLANT": [r"\b(?:planting|planted)\b", r"\b(?:bomb|defuser) planted\b"],
    "DEFUSE": [r"\b(?:defusing|defused)\b", r"\b(?:bomb|defuser) defused\b"],
    "ROUND_WIN": [
        r"\bround victory\b",
        r"\bround won\b",
        r"\byou won the round\b",
        r"\battackers win\b",
        r"\bdefenders win\b",
    ],
    "CLUTCH": [r"\bclutch\b"],
    "FLAWLESS_ROUND": [r"\bflawless(?: victory| round)?\b"],
    "MATCH_POINT": [r"\bmatch point\b"],
    "OVERTIME": [r"\bovertime\b"],
}


def _event_patterns(profile):
    patterns = {key: list(value) for key, value in DEFAULT_EVENT_PATTERNS.items()}
    for event_type, values in profile.get("event_patterns", {}).items():
        if event_type in patterns and isinstance(values, list):
            patterns[event_type].extend(str(value) for value in values)
    return patterns


def parse_game_events(text, player_name="", profile=None, timestamp=None):
    """Parse recognizable HUD text into deduplicated event candidates.

    Kill/death events require a configured player name and a kill-feed row
    containing that name, avoiding treating other players' fights as the user's.
    """
    profile = profile or {}
    player_name = (player_name or "").strip()
    patterns = _event_patterns(profile)
    timestamp = timestamp or datetime.now().isoformat(timespec="seconds")
    events = []
    seen = set()

    for raw_line in (text or "").splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        lower = line.casefold()

        for event_type, event_patterns in patterns.items():
            compiled_patterns = [
                pattern.replace("{player}", re.escape(player_name))
                for pattern in event_patterns
                if "{player}" not in pattern or player_name
            ]
            if any(re.search(pattern, lower, re.IGNORECASE) for pattern in compiled_patterns):
                signature = (event_type, lower)
                if signature not in seen:
                    seen.add(signature)
                    events.append({
                        "type": event_type,
                        "text": line,
                        "timestamp": timestamp,
                    })

        player_match = (
            re.search(
                rf"(?<!\w){re.escape(player_name)}(?!\w)",
                line,
                re.IGNORECASE,
            )
            if player_name
            else None
        )
        if player_match:
            has_attributed_event = any(
                event["text"] == line and event["type"] in ("KILL", "DEATH")
                for event in events
            )
            has_other_event = any(
                event["text"] == line
                and event["type"] not in ("KILL", "DEATH", "HEADSHOT")
                for event in events
            )
            if not has_attributed_event and not has_other_event:
                prefix = line[:player_match.start()].strip()
                suffix = line[player_match.end():].strip()
                inferred_type = (
                    "KILL"
                    if not prefix and suffix
                    else "DEATH"
                    if prefix and not suffix
                    else None
                )
                if inferred_type:
                    signature = (inferred_type, lower)
                    if signature not in seen:
                        seen.add(signature)
                        events.append({
                            "type": inferred_type,
                            "text": line,
                            "timestamp": timestamp,
                        })

            if re.search(r"\bheadshot\b", lower):
                signature = ("HEADSHOT", lower)
                if signature not in seen:
                    seen.add(signature)
                    events.append({
                        "type": "HEADSHOT",
                        "text": line,
                        "timestamp": timestamp,
                    })

    return events


class GameEventMonitor:
    """Poll the primary display's configurable normalized OCR region."""

    def __init__(self, game, region=None, player_name="", tesseract_cmd=""):
        self.game = game
        self.profile = game.get("profile", {})
        self.region = dict(region or self.profile.get("ocr_region") or DEFAULT_OCR_REGION)
        self.player_name = player_name
        self.tesseract_cmd = tesseract_cmd.strip()
        self._last_capture_hash = None
        self._seen_event_times = {}

    def capture_text(self):
        """Take one OCR sample; dependency/configuration errors are explicit."""
        try:
            import mss
            import pytesseract
            from PIL import Image
        except ImportError as error:
            raise RuntimeError(
                "Game OCR requires mss, Pillow, and pytesseract. Install the "
                "application dependencies and Tesseract OCR."
            ) from error

        if self.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd

        with mss.mss() as capture:
            if len(capture.monitors) < 2:
                raise RuntimeError("No primary display is available for game OCR.")
            monitor = capture.monitors[1]
            left = max(0, min(1, float(self.region["left"])))
            top = max(0, min(1, float(self.region["top"])))
            width = max(0, min(1 - left, float(self.region["width"])))
            height = max(0, min(1 - top, float(self.region["height"])))
            box = {
                "left": monitor["left"] + int(monitor["width"] * left),
                "top": monitor["top"] + int(monitor["height"] * top),
                "width": max(1, int(monitor["width"] * width)),
                "height": max(1, int(monitor["height"] * height)),
            }
            screenshot = capture.grab(box)
            image = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
            return pytesseract.image_to_string(image, config="--psm 6")

    def poll_events(self):
        text = self.capture_text()
        capture_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if capture_hash == self._last_capture_hash:
            return []
        self._last_capture_hash = capture_hash
        now = time.monotonic()
        events = parse_game_events(
            text,
            player_name=self.player_name,
            profile=self.profile,
        )
        fresh_events = []
        for event in events:
            signature = (event["type"], event["text"].casefold())
            previous_time = self._seen_event_times.get(signature, 0)
            if now - previous_time < 8:
                continue
            self._seen_event_times[signature] = now
            fresh_events.append(event)

        self._seen_event_times = {
            signature: seen_at
            for signature, seen_at in self._seen_event_times.items()
            if now - seen_at < 60
        }
        return fresh_events

    def run(self, stop_event, event_queue, interval=1.0):
        """Poll until stopped, reporting OCR failures through the same queue."""
        while not stop_event.is_set():
            try:
                for event in self.poll_events():
                    event_queue.put(("event", event))
            except Exception as error:
                event_queue.put(("error", error))
                return
            stop_event.wait(max(float(interval), 0.25))
