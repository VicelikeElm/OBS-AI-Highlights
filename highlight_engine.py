import os

# ctranslate2's native file-opening code can fail to follow the NTFS
# symlinks huggingface_hub's cache normally uses on Windows - "Unable
# to open file 'model.bin'" even though the file is genuinely there and
# readable by Python's own open(). Disabling symlinks makes the cache
# use real file copies instead, avoiding that whole class of failure.
# Must be set before faster_whisper (and the huggingface_hub it pulls
# in) is imported below.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

import gc
import re
import time
import wave
import shutil
import tempfile
import subprocess
import logging
import threading
import queue
import statistics
from pathlib import Path
from datetime import datetime

import pyaudiowpatch as pyaudio
import obsws_python as obs
import numpy as np

from faster_whisper import WhisperModel

import socket

import config as app_config
import presets
import remote_api
import session_stats


# =========================================================
# QUIET LIBRARY LOGGING
# =========================================================

# Prevent OBS connection attempts from filling the log with
# harmless tracebacks while OBS is not running.
logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
logging.getLogger("websocket").setLevel(logging.CRITICAL)


# =========================================================
# CONFIG + ACTIVE PRESET
# =========================================================

CONFIG = app_config.load_config()

ACTIVE_PRESET = presets.get_preset(
    CONFIG.get("preset", presets.DEFAULT_PRESET),
    CONFIG.get("custom_profiles"),
)

# Read once at startup, same as every other config value here - toggling
# these in the app while a capture is already running takes effect on
# the next run, not mid-session.
AUTO_VERIFY = bool(CONFIG.get("auto_verify", True))
AUTO_RENDER = bool(CONFIG.get("auto_render", True))

REMOTE_API_ENABLED = bool(CONFIG.get("remote_api_enabled", True))
REMOTE_API_PORT = int(CONFIG.get("remote_api_port", 8756))

# Each rule: {"match": "<substring>", "action": "pause" | "ignore" | "preset",
# "preset": "<preset key, only for action=preset>"}. Matching is a
# case-insensitive substring check against OBS's current program scene
# name - see evaluate_scene_rules() below.
SCENE_RULES = CONFIG.get("scene_rules", [])

# A "spike" is how many times louder a moment was than the recent
# rolling baseline - not an absolute loudness, so it adapts to however
# loud/quiet this particular stream normally is. See
# compute_chunk_loudness() and the AUDIO EXCITEMENT section in
# run_live_sermon() below.
AUDIO_EXCITEMENT_ENABLED = bool(CONFIG.get("audio_excitement_enabled", True))
AUDIO_EXCITEMENT_MODERATE_RATIO = float(CONFIG.get("audio_excitement_moderate_ratio", 1.6))
AUDIO_EXCITEMENT_STRONG_RATIO = float(CONFIG.get("audio_excitement_strong_ratio", 2.5))
AUDIO_EXCITEMENT_MODERATE_BONUS = int(CONFIG.get("audio_excitement_moderate_bonus", 10))
AUDIO_EXCITEMENT_STRONG_BONUS = int(CONFIG.get("audio_excitement_strong_bonus", 18))

# How many recent chunks (each CHUNK_SECONDS long) the rolling loudness
# baseline is computed from.
LOUDNESS_HISTORY_SIZE = 10


# =========================================================
# ENVIRONMENT
# =========================================================

OBS_HOST = CONFIG.get("obs_host", "127.0.0.1")
OBS_PORT = int(CONFIG.get("obs_port", 4455))
OBS_PASSWORD = app_config.get_obs_password()


# =========================================================
# PATHS
# =========================================================

BASE_FOLDER = str(Path(__file__).resolve().parent)

SHORTS_ROOT = str(app_config.get_output_folder(CONFIG))

RAW_FOLDER = os.path.join(
    SHORTS_ROOT,
    "Raw"
)

TRANSCRIPT_FOLDER = os.path.join(
    SHORTS_ROOT,
    "Transcripts"
)

# This must match the folder OBS itself initially writes
# Replay Buffer recordings into.
OBS_REPLAY_FOLDER = CONFIG.get(
    "recording_folder",
    app_config.DEFAULTS["recording_folder"],
)

# =========================================================
# AUDIO
# =========================================================

# Configured per-install, since every machine's loopback device is
# named differently. Falls back to a numeric device index if the name
# doesn't match anything (e.g. on first run before it's configured).
AUDIO_DEVICE_NAME = CONFIG.get("audio_device_name", "")

AUDIO_DEVICE_FALLBACK = int(
    CONFIG.get("audio_device_fallback_index", 0)
)

# "loopback" (default) - what's being sent to an output/monitor device,
# i.e. OBS's own audio mix - or "microphone" - a genuine input device,
# for transcribing just a mic instead of the whole program mix.
AUDIO_SOURCE_TYPE = CONFIG.get(
    "audio_source_type", app_config.DEFAULTS["audio_source_type"]
)


# =========================================================
# LIVE WHISPER
# =========================================================

WHISPER_MODEL = CONFIG.get("whisper_model", "small")
WHISPER_DEVICE = CONFIG.get("whisper_device", "cuda")
WHISPER_COMPUTE_TYPE = CONFIG.get("whisper_compute_type", "float16")

CHUNK_SECONDS = 6


# =========================================================
# THOUGHT DETECTION
# =========================================================

MIN_THOUGHT_SECONDS = 24
TARGET_THOUGHT_SECONDS = 35
MAX_THOUGHT_SECONDS = 65

SILENCE_CHUNKS_TO_FINISH = 1


# =========================================================
# CLIP SCORING
# =========================================================

POSSIBLE_THRESHOLD = int(CONFIG.get("possible_threshold", 55))
SAVE_THRESHOLD = int(CONFIG.get("save_threshold", 70))

MIN_SECONDS_BETWEEN_SAVES = 75

SAVE_DELAY_SECONDS = 5


# =========================================================
# CONTROLLER TIMING
# =========================================================

OBS_SEARCH_INTERVAL = 5

# After all OBS output activity ends, wait this long before
# loading Medium Whisper and processing Shorts.
POST_SERVICE_DELAY = 90

# How frequently to check whether OBS becomes active again.
IDLE_CHECK_INTERVAL = 2

# How long to wait for OBS to finish creating a replay file.
REPLAY_FILE_WAIT_SECONDS = 20


# =========================================================
# CONTENT LANGUAGE (from the active preset - see presets.py)
# =========================================================

STRONG_PHRASES = ACTIVE_PRESET["strong_phrases"]
APPLICATION_PHRASES = ACTIVE_PRESET["application_phrases"]
SCRIPTURE_PHRASES = ACTIVE_PRESET["reference_phrases"]
ADMIN_PHRASES = ACTIVE_PRESET["low_value_phrases"]
PRAYER_START_PHRASES = ACTIVE_PRESET["pause_phrases"]
PAUSE_END_PATTERN = ACTIVE_PRESET["pause_end_pattern"] or r"(?!)"  # never matches if unset
WHISPER_INITIAL_PROMPT = ACTIVE_PRESET["whisper_initial_prompt"]


def apply_preset(name):
    """Switches the active preset at runtime (e.g. via the remote API's
    POST /preset/<name>) by reassigning these same module globals that
    score_sermon_moment()/update_prayer_state() already read by name -
    called only from run_live_sermon()'s own thread, never directly from
    the HTTP handler thread, so scoring never sees a half-updated state.
    presets.get_preset() already falls back gracefully for an unknown
    name rather than raising, so this can't crash the capture loop."""
    global ACTIVE_PRESET, STRONG_PHRASES, APPLICATION_PHRASES
    global SCRIPTURE_PHRASES, ADMIN_PHRASES, PRAYER_START_PHRASES
    global PAUSE_END_PATTERN, WHISPER_INITIAL_PROMPT

    new_preset = presets.get_preset(name, CONFIG.get("custom_profiles"))

    ACTIVE_PRESET = new_preset
    STRONG_PHRASES = new_preset["strong_phrases"]
    APPLICATION_PHRASES = new_preset["application_phrases"]
    SCRIPTURE_PHRASES = new_preset["reference_phrases"]
    ADMIN_PHRASES = new_preset["low_value_phrases"]
    PRAYER_START_PHRASES = new_preset["pause_phrases"]
    PAUSE_END_PATTERN = new_preset["pause_end_pattern"] or r"(?!)"
    WHISPER_INITIAL_PROMPT = new_preset["whisper_initial_prompt"]

    return new_preset["label"]


def evaluate_scene_rules(scene_name, rules):
    """Returns the first rule whose "match" text appears in scene_name
    (case-insensitive substring), or None if nothing matches. Only ever
    reads config-derived data - can't raise, so it's always safe to call
    from inside the live loop."""
    if not scene_name:
        return None

    scene_name_lower = scene_name.lower()

    for rule in rules:

        match_text = str(
            rule.get("match", "")
        ).strip()

        if match_text and match_text.lower() in scene_name_lower:
            return rule

    return None


# =========================================================
# SIMPLE OUTPUT
# =========================================================

def log(message=""):
    print(message, flush=True)


# =========================================================
# FOLDERS
# =========================================================

def ensure_folders():

    os.makedirs(
        RAW_FOLDER,
        exist_ok=True
    )

    os.makedirs(
        TRANSCRIPT_FOLDER,
        exist_ok=True
    )


# =========================================================
# AUDIO DEVICE DISCOVERY
# =========================================================

def find_loopback_device(audio):

    is_microphone_mode = AUDIO_SOURCE_TYPE == "microphone"

    exact_matches = []
    partial_matches = []

    for index in range(
        audio.get_device_count()
    ):

        info = (
            audio.get_device_info_by_index(
                index
            )
        )

        name = info.get(
            "name",
            ""
        )

        if name == AUDIO_DEVICE_NAME:
            exact_matches.append(index)

        elif is_microphone_mode:
            # No hardcoded name guess here - every machine's microphone
            # is named differently, unlike OBS's own default monitor
            # device. A genuine mic is any non-loopback input device.
            if (
                info.get("maxInputChannels", 0) > 0
                and not info.get("isLoopbackDevice", False)
            ):
                partial_matches.append(index)

        elif (
            "Headphones"
            in name
            and
            "High Definition Audio Device"
            in name
            and
            "Loopback"
            in name
        ):
            partial_matches.append(index)

    if exact_matches:

        return exact_matches[0]

    if partial_matches:

        return partial_matches[0]

    # Last resort
    try:

        info = (
            audio.get_device_info_by_index(
                AUDIO_DEVICE_FALLBACK
            )
        )

        if (
            info.get(
                "maxInputChannels",
                0
            )
            > 0
        ):

            return AUDIO_DEVICE_FALLBACK

    except Exception:
        pass

    if is_microphone_mode:
        raise RuntimeError(
            "Could not find a microphone/input device. "
            "Select one in Settings."
        )

    raise RuntimeError(
        "Could not find the OBS monitor "
        "Headphones loopback device."
    )


def open_loopback_stream():
    """
    Creates a fresh PyAudio instance + loopback stream. Used both at
    startup and to recover after the audio stream stalls out.
    """

    audio = pyaudio.PyAudio()

    device_index = find_loopback_device(audio)

    device_info = audio.get_device_info_by_index(
        device_index
    )

    rate = int(
        device_info["defaultSampleRate"]
    )

    channels = int(
        device_info["maxInputChannels"]
    )

    stream = audio.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=rate,
        input=True,
        input_device_index=device_index,
        frames_per_buffer=1024,
    )

    return audio, stream, device_index, rate, channels


def read_stream_chunk(
    stream,
    frame_count,
    timeout_seconds=15.0
):
    """
    stream.read() is a blocking native (WASAPI) call. If the underlying
    Windows Audio session dies mid-service -- observed 2026-08-30, when
    a crash in Elgato's audio routing server (ElgatoAudioControlServer
    .exe / WindowsAudioRouterApi.dll) took down the whole Windows Audio
    service -- the call never returns and never raises. It just hangs
    forever, silently ending live clip detection for the rest of the
    service. Run the read on a background thread and time it out
    instead of blocking the live loop indefinitely.
    """

    result = queue.Queue(
        maxsize=1
    )

    def _read():

        try:
            result.put(
                (
                    "ok",
                    stream.read(
                        frame_count,
                        exception_on_overflow=False,
                    ),
                )
            )

        except Exception as error:
            result.put(
                (
                    "error",
                    error
                )
            )

    threading.Thread(
        target=_read,
        daemon=True
    ).start()

    try:
        status, payload = result.get(
            timeout=timeout_seconds
        )

    except queue.Empty:
        raise TimeoutError(
            "Audio stream stalled (no data for "
            f"{timeout_seconds:.0f}s) -- Windows Audio may "
            "have restarted underneath the capture stream."
        )

    if status == "error":
        raise payload

    return payload


def compute_chunk_loudness(frames):
    """RMS loudness of a chunk of raw 16-bit PCM audio frames (the same
    frames read from the loopback stream), normalized to roughly 0.0-1.0
    against int16's max magnitude. No new dependency - numpy is already
    bundled transitively via faster-whisper/ctranslate2."""
    raw = b"".join(frames)

    if not raw:
        return 0.0

    samples = np.frombuffer(raw, dtype=np.int16)

    if samples.size == 0:
        return 0.0

    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))

    return float(rms / 32768.0)


# =========================================================
# OBS CONNECTION
# =========================================================

def obs_port_is_open():

    try:

        with socket.create_connection(
            (OBS_HOST, OBS_PORT),
            timeout=1
        ):
            return True

    except OSError:
        return False


def try_connect_obs():

    # Don't even invoke obsws-python until
    # something is actually listening on 4455.
    if not obs_port_is_open():
        return None

    try:

        # OBS can open the WebSocket port while
        # the rest of OBS is still initializing.
        client = obs.ReqClient(
            host=OBS_HOST,
            port=OBS_PORT,
            password=OBS_PASSWORD,
            timeout=3,
        )

        client.get_version()

        return client

    except Exception:

        # OBS exists but isn't quite ready yet.
        return None


def wait_for_obs():

    log("Waiting for OBS...")

    while True:

        client = try_connect_obs()

        if client is not None:

            log("OBS is ready.")
            log("OBS WebSocket connected.")

            return client

        time.sleep(
            OBS_SEARCH_INTERVAL
        )


# =========================================================
# OBS OUTPUT STATUS
# =========================================================

def read_obs_status(client):

    try:

        stream_status = (
            client.get_stream_status()
        )

        record_status = (
            client.get_record_status()
        )

        replay_status = (
            client.get_replay_buffer_status()
        )

        streaming = bool(
            getattr(
                stream_status,
                "output_active",
                False
            )
        )

        recording = bool(
            getattr(
                record_status,
                "output_active",
                False
            )
        )

        replay = bool(
            getattr(
                replay_status,
                "output_active",
                False
            )
        )

        scene_name = ""

        try:

            scene_status = (
                client.get_current_program_scene()
            )

            scene_name = str(
                getattr(
                    scene_status,
                    "scene_name",
                    ""
                )
            )

        except Exception:

            # Scene lookup is a nice-to-have signal, not a required one -
            # never let it take down the rest of the status check.
            pass

        return {
            "connected": True,
            "streaming": streaming,
            "recording": recording,
            "replay": replay,
            "scene_name": scene_name,
        }

    except Exception:

        return {
            "connected": False,
            "streaming": False,
            "recording": False,
            "replay": False,
            "scene_name": "",
        }


def obs_has_activity(status):

    return (
        status["streaming"]
        or status["recording"]
        or status["replay"]
    )


def main_service_running(status):

    return (
        status["streaming"]
        or status["recording"]
    )


# =========================================================
# WAIT FOR SERVICE ACTIVITY
# =========================================================

def wait_for_service_activity():

    client = None

    while True:

        if client is None:

            client = wait_for_obs()

            log()
            log(
                "Waiting for OBS recording, "
                "streaming, or Replay Buffer..."
            )

        status = read_obs_status(
            client
        )

        if not status["connected"]:

            log("OBS closed.")
            client = None

            time.sleep(
                OBS_SEARCH_INTERVAL
            )

            continue

        if obs_has_activity(status):

            log()
            log("OBS activity detected.")

            if status["streaming"]:
                log("  Streaming: ON")

            if status["recording"]:
                log("  Recording: ON")

            if status["replay"]:
                log("  Replay Buffer: ON")

            return client, status

        time.sleep(2)


# =========================================================
# REPLAY BUFFER CONTROL
# =========================================================

def ensure_replay_buffer(
    client,
    status
):

    # Replay already running.
    if status["replay"]:

        return False

    # If recording/streaming begins, automatically
    # enable Replay Buffer for AI clipping.
    if main_service_running(status):

        try:

            log(
                "Starting Replay Buffer "
                "for Sermon AI..."
            )

            client.start_replay_buffer()

            time.sleep(2)

            return True

        except Exception as error:

            log(
                "WARNING: Could not start "
                "Replay Buffer."
            )

            log(str(error))

    return False


def stop_auto_replay(
    client,
    auto_started
):

    if not auto_started:
        return

    try:

        status = read_obs_status(
            client
        )

        if (
            status["connected"]
            and status["replay"]
        ):

            log(
                "Stopping Sermon AI's "
                "Replay Buffer..."
            )

            client.stop_replay_buffer()

            time.sleep(1)

    except Exception:
        pass


# =========================================================
# REPLAY FILE HELPERS
# =========================================================

def get_media_files(folder):

    if not os.path.exists(folder):

        return []

    extensions = (
        ".mp4",
        ".mkv",
        ".mov",
        ".ts",
        ".m4v",
    )

    results = []

    for name in os.listdir(folder):

        path = os.path.join(
            folder,
            name
        )

        if (
            os.path.isfile(path)
            and
            name.lower().endswith(
                extensions
            )
        ):

            results.append(path)

    return results


def snapshot_replay_files():

    return set(
        get_media_files(
            OBS_REPLAY_FOLDER
        )
    )


def wait_for_new_replay(
    before_files
):

    deadline = (
        time.time()
        +
        REPLAY_FILE_WAIT_SECONDS
    )

    while time.time() < deadline:

        time.sleep(0.5)

        after_files = set(
            get_media_files(
                OBS_REPLAY_FOLDER
            )
        )

        new_files = list(
            after_files
            -
            before_files
        )

        if not new_files:
            continue

        newest = max(
            new_files,
            key=os.path.getmtime
        )

        previous_size = -1
        stable_checks = 0

        for _ in range(40):

            try:

                size = os.path.getsize(
                    newest
                )

            except OSError:

                time.sleep(0.5)
                continue

            if (
                size == previous_size
                and
                size > 0
            ):

                stable_checks += 1

            else:

                stable_checks = 0

            if stable_checks >= 2:

                return newest

            previous_size = size

            time.sleep(0.5)

        return newest

    return None


# =========================================================
# CLIP FILES
# =========================================================

def move_replay(
    source_path,
    clip_number,
    score
):

    extension = os.path.splitext(
        source_path
    )[1]

    stamp = datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S"
    )

    filename = (
        f"{stamp}_AI_Clip_"
        f"{clip_number:02d}"
        f"_Score_{score}"
        f"{extension}"
    )

    destination = os.path.join(
        RAW_FOLDER,
        filename
    )

    shutil.move(
        source_path,
        destination
    )

    return destination


def save_candidate_transcript(
    clip_path,
    score,
    text,
    reasons,
    duration
):

    base_name = os.path.splitext(
        os.path.basename(
            clip_path
        )
    )[0]

    destination = os.path.join(
        TRANSCRIPT_FOLDER,
        base_name + ".txt"
    )

    with open(
        destination,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(
            "SERMON AI CANDIDATE\n"
        )

        file.write(
            "=" * 70 + "\n\n"
        )

        file.write(
            f"Created: "
            f"{datetime.now()}\n"
        )

        file.write(
            f"Score: {score}\n"
        )

        file.write(
            f"Thought duration: "
            f"{duration:.1f} seconds\n"
        )

        file.write(
            f"Raw replay: "
            f"{clip_path}\n"
        )

        file.write(
            "\nReasons:\n"
        )

        for reason in reasons:

            file.write(
                f"- {reason}\n"
            )

        file.write(
            "\nLIVE WHISPER TRANSCRIPT\n"
        )

        file.write(
            "-" * 70 + "\n"
        )

        file.write(text.strip())

        file.write("\n")

    return destination


# =========================================================
# TEXT HELPERS
# =========================================================

def contains_any(
    text,
    phrases
):

    lower = text.lower()

    return any(
        phrase in lower
        for phrase in phrases
    )


def count_phrase_hits(
    text,
    phrases
):
    """Returns (count, matched_phrases) - matched_phrases lets the
    session-stats layer track which specific phrases are actually
    driving scores, for tuning presets against real data instead of
    guessing."""

    lower = text.lower()

    matched = [
        phrase
        for phrase in phrases
        if phrase in lower
    ]

    return len(matched), matched


# =========================================================
# PRAYER DETECTION
# =========================================================

def update_prayer_state(
    text,
    in_prayer
):
    """Generalizes the original prayer-suspend mechanism: once a
    pause_phrases match is seen, clip candidacy is fully suspended
    until pause_end_pattern matches (e.g. "amen" for the church preset,
    "i'm back" for gaming/twitch)."""

    lower = text.lower()

    if not in_prayer:

        if contains_any(
            lower,
            PRAYER_START_PHRASES
        ):

            log()
            log(
                "*** PAUSE DETECTED "
                "- clipping paused ***"
            )

            return True

        return False

    # Once paused, the preset's end pattern is the signal to resume.
    if re.search(
        PAUSE_END_PATTERN,
        lower
    ):

        log()
        log(
            "*** PAUSE ENDED "
            "- clipping resumed ***"
        )

        return False

    return True


# =========================================================
# THOUGHT DETECTION
# =========================================================

def ends_like_sentence(text):

    text = text.strip()

    if not text:
        return False

    return text.endswith(
        (".", "!", "?")
    )


def should_finish_thought(
    duration,
    latest_text,
    silence_chunks
):

    if (
        duration
        >= MIN_THOUGHT_SECONDS
        and
        silence_chunks
        >= SILENCE_CHUNKS_TO_FINISH
    ):

        return True

    if (
        duration
        >= TARGET_THOUGHT_SECONDS
        and
        ends_like_sentence(
            latest_text
        )
    ):

        return True

    if duration >= MAX_THOUGHT_SECONDS:

        return True

    return False


# =========================================================
# SERMON SCORING
# =========================================================

def score_sermon_moment(
    text,
    duration,
    audio_excitement=0.0
):

    clean = text.strip()

    if not clean:

        return 0, [], [], []

    lower = clean.lower()

    word_count = len(
        clean.split()
    )

    score = 0
    reasons = []
    matched_phrases = []

    # Duration
    if 30 <= duration <= 55:

        score += 20

        reasons.append(
            "excellent clip duration"
        )

    elif 24 <= duration <= 65:

        score += 14

        reasons.append(
            "usable clip duration"
        )

    # Speech amount
    if 65 <= word_count <= 160:

        score += 15

        reasons.append(
            "good amount of speech"
        )

    elif 40 <= word_count < 65:

        score += 8

    # Complete ending
    if clean.endswith(
        (".", "!", "?")
    ):

        score += 8

        reasons.append(
            "complete ending"
        )

    # Strong sermon language
    hits, hit_phrases = count_phrase_hits(
        clean,
        STRONG_PHRASES
    )

    if hits:

        score += min(
            hits * 7,
            21
        )

        reasons.append(
            f"{hits} strong sermon phrase(s)"
        )

        matched_phrases.extend(
            hit_phrases
        )

    # Application
    hits, hit_phrases = count_phrase_hits(
        clean,
        APPLICATION_PHRASES
    )

    if hits:

        score += min(
            hits * 7,
            18
        )

        reasons.append(
            f"{hits} application phrase(s)"
        )

        matched_phrases.extend(
            hit_phrases
        )

    # Scripture
    hits, hit_phrases = count_phrase_hits(
        clean,
        SCRIPTURE_PHRASES
    )

    if hits:

        score += min(
            hits * 4,
            12
        )

        reasons.append(
            "Scripture connection"
        )

        matched_phrases.extend(
            hit_phrases
        )

    # Structure
    if "?" in clean:

        score += 5

        reasons.append(
            "rhetorical question"
        )

    if "!" in clean:

        score += 3

    if "therefore" in lower:

        score += 6

        reasons.append(
            "conclusion"
        )

    if "because" in lower:

        score += 4

    if (
        "but" in lower
        and
        "because" in lower
    ):

        score += 4

    if "so that" in lower:

        score += 4

    contrast_words = [
        "but",
        "yet",
        "instead",
        "however",
        "rather",
    ]

    contrast_hits = sum(
        1
        for word in contrast_words
        if
        f" {word} "
        in
        f" {lower} "
    )

    if contrast_hits >= 2:

        score += 5

        reasons.append(
            "strong contrast"
        )

    # Administrative material
    admin_hits, admin_hit_phrases = count_phrase_hits(
        clean,
        ADMIN_PHRASES
    )

    if admin_hits:

        penalty = min(
            admin_hits * 25,
            60
        )

        score -= penalty

        reasons.append(
            f"-{penalty} admin"
        )

    if word_count < 35:

        score -= 20

        reasons.append(
            "too little context"
        )

    # Audio excitement - a sudden loudness spike (cheering, shouting,
    # applause, laughter) relative to the recent rolling baseline. Comes
    # after the "too little context" penalty above so a short-but-loud
    # reaction (exactly the kind of clip this is meant to catch) can
    # still clear the bar even at low word counts.
    if AUDIO_EXCITEMENT_ENABLED:

        if audio_excitement >= AUDIO_EXCITEMENT_STRONG_RATIO:

            score += AUDIO_EXCITEMENT_STRONG_BONUS

            reasons.append(
                "audio excitement (loud reaction)"
            )

        elif audio_excitement >= AUDIO_EXCITEMENT_MODERATE_RATIO:

            score += AUDIO_EXCITEMENT_MODERATE_BONUS

            reasons.append(
                "audio excitement (loudness spike)"
            )

    score = max(
        0,
        min(
            score,
            100
        )
    )

    return score, reasons, matched_phrases, admin_hit_phrases


# =========================================================
# SAVE REPLAY
# =========================================================

def save_obs_replay(
    client
):

    status = read_obs_status(
        client
    )

    if (
        not status["connected"]
        or
        not status["replay"]
    ):

        log(
            "Replay Buffer is not running. "
            "Cannot save this candidate."
        )

        return client, None

    before_files = (
        snapshot_replay_files()
    )

    try:

        client.save_replay_buffer()

    except Exception:

        log(
            "OBS connection was lost "
            "while saving."
        )

        return None, None

    replay_path = (
        wait_for_new_replay(
            before_files
        )
    )

    return client, replay_path


def save_triggered_clip(client, clip_number, score, thought_text, reasons, thought_duration):
    """Runs the same replay-buffer-save + move + transcript-write
    sequence for a clip that's been decided worth saving - whether that
    decision came from the normal phrase-scoring path or a manual
    POST /highlight request via the remote API (see run_live_sermon()'s
    two call sites below). Returns (client, clip_number, last_save_time
    or None, stop_session, clip_path or None) - stop_session=True means
    the OBS connection was lost and the caller should end its loop."""

    log(
        f"Waiting "
        f"{SAVE_DELAY_SECONDS}s "
        f"before saving..."
    )

    time.sleep(
        SAVE_DELAY_SECONDS
    )

    log(
        "Saving OBS Replay Buffer..."
    )

    client, replay_path = (
        save_obs_replay(
            client
        )
    )

    if client is None:

        log(
            "OBS connection lost."
        )

        return client, clip_number, None, True, None

    if replay_path is None:

        log(
            "WARNING: Replay was "
            "not located."
        )

        return client, clip_number, None, False, None

    clip_number += 1

    clip_path = move_replay(
        replay_path,
        clip_number,
        score,
    )

    transcript_path = (
        save_candidate_transcript(
            clip_path,
            score,
            thought_text,
            reasons,
            thought_duration,
        )
    )

    log()
    log("CLIP SAVED:")
    log(clip_path)

    log()
    log(
        "TRANSCRIPT SAVED:"
    )

    log(
        transcript_path
    )

    return client, clip_number, time.time(), False, str(clip_path)


# =========================================================
# LIVE SERMON SESSION
# =========================================================

def run_live_sermon(
    client,
    initial_status,
    remote_state=None
):

    log()
    log("=" * 70)
    log("LIVE SERMON AI STARTING")
    log("=" * 70)

    session_id = session_stats.start_session(
        ACTIVE_PRESET.get(
            "label",
            ""
        )
    )

    log(
        f"Session: {session_id}"
    )

    auto_started_replay = (
        ensure_replay_buffer(
            client,
            initial_status
        )
    )

    # -----------------------------------------------------
    # LOAD SMALL MODEL
    # -----------------------------------------------------

    log()
    log(
        "Loading live Whisper "
        "model: small"
    )

    model = WhisperModel(
        WHISPER_MODEL,
        device=WHISPER_DEVICE,
        compute_type=(
            WHISPER_COMPUTE_TYPE
        ),
    )

    log(
        "Live Whisper loaded."
    )

    # -----------------------------------------------------
    # AUDIO
    # -----------------------------------------------------

    audio, stream, device_index, rate, channels = (
        open_loopback_stream()
    )

    log()
    log(
        f"Listening to device "
        f"{device_index}: "
        f"{audio.get_device_info_by_index(device_index)['name']}"
    )

    # -----------------------------------------------------
    # SESSION STATE
    # -----------------------------------------------------

    thought_parts = []
    thought_start_time = None
    silence_chunks = 0

    last_save_time = 0
    clip_number = 0

    in_prayer = False

    scene_rule_action = None
    last_scene_preset_applied = None

    loudness_history = []
    thought_max_spike_ratio = 0.0

    session_running = True

    try:

        while session_running:

            # ---------------------------------------------
            # CHECK OBS FIRST
            # ---------------------------------------------

            status = read_obs_status(
                client
            )

            if not status["connected"]:

                log()
                log(
                    "OBS disconnected."
                )

                session_running = False
                break

            # If we started Replay Buffer automatically,
            # stop it once stream + recording have ended.
            if (
                auto_started_replay
                and
                not main_service_running(
                    status
                )
            ):

                stop_auto_replay(
                    client,
                    True
                )

                auto_started_replay = False

                status = read_obs_status(
                    client
                )

            # If nothing OBS-related is active anymore,
            # the live session is finished.
            if not obs_has_activity(
                status
            ):

                log()
                log(
                    "All OBS output activity "
                    "has stopped."
                )

                session_running = False
                break

            # If recording/streaming starts but replay
            # somehow stopped, restart it.
            if (
                main_service_running(status)
                and
                not status["replay"]
            ):

                auto_started_replay = (
                    ensure_replay_buffer(
                        client,
                        status
                    )
                    or
                    auto_started_replay
                )

            # ---------------------------------------------
            # SCENE RULES
            # ---------------------------------------------

            scene_rule = evaluate_scene_rules(
                status.get(
                    "scene_name",
                    ""
                ),
                SCENE_RULES,
            )

            scene_rule_action = (
                scene_rule["action"]
                if scene_rule
                else None
            )

            if scene_rule_action == "preset":

                requested_scene_preset = scene_rule.get(
                    "preset"
                )

                if (
                    requested_scene_preset
                    and requested_scene_preset != last_scene_preset_applied
                ):

                    new_label = apply_preset(
                        requested_scene_preset
                    )

                    last_scene_preset_applied = requested_scene_preset

                    log()
                    log(
                        f"Preset switched via scene rule "
                        f"('{status.get('scene_name', '')}'): {new_label}"
                    )

            # ---------------------------------------------
            # REMOTE CONTROL: preset switch / manual highlight
            # ---------------------------------------------

            if remote_state is not None:

                remote_state.update_status(
                    capturing=True,
                    preset=ACTIVE_PRESET.get("label"),
                    clips_saved=clip_number,
                    session_id=session_id,
                )

                requested_preset = (
                    remote_state.consume_preset_switch_request()
                )

                if requested_preset:

                    new_label = apply_preset(
                        requested_preset
                    )

                    log()
                    log(
                        f"Preset switched via remote API: {new_label}"
                    )

                if remote_state.consume_manual_highlight_request():

                    log()
                    log(
                        "Manual highlight requested via remote API."
                    )

                    client, clip_number, new_last_save_time, stop_session, saved_clip_path = (
                        save_triggered_clip(
                            client,
                            clip_number,
                            100,
                            "(manually triggered clip)",
                            ["Manual highlight via remote API"],
                            0.0,
                        )
                    )

                    if stop_session:
                        session_running = False
                        break

                    if new_last_save_time is not None:
                        last_save_time = new_last_save_time

                    if saved_clip_path:
                        session_stats.record_saved_clip(session_id, saved_clip_path)
                        session_stats.record_thought(session_id, "saved", 100, [], [])

                    continue

            # ---------------------------------------------
            # CAPTURE 6 SEC
            # ---------------------------------------------

            frames = []

            chunks_to_read = int(
                rate
                /
                1024
                *
                CHUNK_SECONDS
            )

            try:

                for _ in range(
                    chunks_to_read
                ):

                    data = read_stream_chunk(
                        stream,
                        1024
                    )

                    frames.append(data)

            except Exception as error:

                log()
                log(
                    "WARNING: Audio capture stalled "
                    f"({error}). Reopening the loopback "
                    "device..."
                )

                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass

                try:
                    audio.terminate()
                except Exception:
                    pass

                reopened = False

                for attempt in range(6):

                    try:
                        (
                            audio,
                            stream,
                            device_index,
                            rate,
                            channels,
                        ) = open_loopback_stream()

                        reopened = True
                        break

                    except Exception as reopen_error:

                        log(
                            f"Retry {attempt + 1}/6: still "
                            f"cannot reopen audio device "
                            f"({reopen_error})."
                        )

                        time.sleep(5)

                if reopened:
                    log(
                        "Audio device reopened. Resuming "
                        "live listening."
                    )
                else:
                    log(
                        "Could not recover the audio "
                        "device. Ending live listening for "
                        "this service."
                    )

                    session_running = False
                    break

                continue

            # ---------------------------------------------
            # AUDIO EXCITEMENT
            # ---------------------------------------------

            # Computed on every chunk (speech or silence) so the rolling
            # baseline reflects this stream's actual ambient loudness,
            # not just its speech. thought_max_spike_ratio tracks the
            # loudest moment across whichever chunks end up making up
            # the current thought - reset alongside thought_parts below.

            chunk_loudness = compute_chunk_loudness(
                frames
            )

            if loudness_history:

                baseline_loudness = statistics.median(
                    loudness_history
                )

            else:

                baseline_loudness = 0.0

            if baseline_loudness > 0.01:

                spike_ratio = (
                    chunk_loudness
                    /
                    baseline_loudness
                )

            else:

                spike_ratio = 0.0

            thought_max_spike_ratio = max(
                thought_max_spike_ratio,
                spike_ratio
            )

            loudness_history.append(
                chunk_loudness
            )

            if len(loudness_history) > LOUDNESS_HISTORY_SIZE:

                loudness_history.pop(0)

            # ---------------------------------------------
            # TEMP AUDIO
            # ---------------------------------------------

            with tempfile.NamedTemporaryFile(
                suffix=".wav",
                delete=False
            ) as temp_file:

                temp_path = (
                    temp_file.name
                )

            with wave.open(
                temp_path,
                "wb"
            ) as wf:

                wf.setnchannels(
                    channels
                )

                wf.setsampwidth(
                    audio.get_sample_size(
                        pyaudio.paInt16
                    )
                )

                wf.setframerate(
                    rate
                )

                wf.writeframes(
                    b"".join(frames)
                )

            # ---------------------------------------------
            # TRANSCRIBE
            # ---------------------------------------------

            chunk_text = ""

            try:

                segments, _ = (
                    model.transcribe(
                        temp_path,
                        language="en",
                        vad_filter=True,
                        beam_size=5,
                    )
                )

                pieces = []

                for segment in segments:

                    text = (
                        segment.text
                        .strip()
                    )

                    if text:

                        pieces.append(
                            text
                        )

                chunk_text = " ".join(
                    pieces
                )

            finally:

                try:

                    os.remove(
                        temp_path
                    )

                except OSError:

                    pass

            now = time.time()

            process_thought = False

            # ---------------------------------------------
            # SILENCE
            # ---------------------------------------------

            if not chunk_text:

                if thought_parts:

                    silence_chunks += 1

                    duration = (
                        now
                        -
                        thought_start_time
                    )

                    process_thought = (
                        should_finish_thought(
                            duration,
                            "",
                            silence_chunks,
                        )
                    )

                if not process_thought:

                    continue

            else:

                silence_chunks = 0

                clock = (
                    datetime.now()
                    .strftime(
                        "%H:%M:%S"
                    )
                )

                log(
                    f"[{clock}] "
                    f"{chunk_text}"
                )

                # -----------------------------------------
                # SCENE RULE: IGNORE
                # -----------------------------------------

                # A scene like "Starting Soon" should never let this
                # chunk contribute to a scored thought - unlike pause
                # (checked later, near the save decision), this clears
                # any in-progress thought so nothing from before the
                # ignored scene bleeds into content scored afterward.
                if scene_rule_action == "ignore":

                    thought_parts = []
                    thought_start_time = None
                    thought_max_spike_ratio = 0.0

                    continue

                # -----------------------------------------
                # PRAYER MODE
                # -----------------------------------------

                previous_prayer = (
                    in_prayer
                )

                in_prayer = (
                    update_prayer_state(
                        chunk_text,
                        in_prayer
                    )
                )

                # Still inside prayer
                if in_prayer:

                    thought_parts = []
                    thought_start_time = None
                    thought_max_spike_ratio = 0.0

                    continue

                # Prayer just ended. Don't use the Amen
                # chunk as a new candidate.
                if (
                    previous_prayer
                    and
                    not in_prayer
                ):

                    thought_parts = []
                    thought_start_time = None
                    thought_max_spike_ratio = 0.0

                    continue

                # -----------------------------------------
                # THOUGHT
                # -----------------------------------------

                if thought_start_time is None:

                    thought_start_time = now

                thought_parts.append(
                    chunk_text
                )

                duration = (
                    now
                    -
                    thought_start_time
                    +
                    CHUNK_SECONDS
                )

                process_thought = (
                    should_finish_thought(
                        duration,
                        chunk_text,
                        silence_chunks,
                    )
                )

                if not process_thought:

                    continue

            # ---------------------------------------------
            # COMPLETE THOUGHT
            # ---------------------------------------------

            if not thought_parts:

                continue

            thought_text = " ".join(
                thought_parts
            ).strip()

            thought_duration = (
                now
                -
                thought_start_time
                +
                CHUNK_SECONDS
            )

            thought_excitement = (
                thought_max_spike_ratio
            )

            # Start fresh immediately
            thought_parts = []
            thought_start_time = None
            silence_chunks = 0
            thought_max_spike_ratio = 0.0

            if (
                thought_duration
                <
                MIN_THOUGHT_SECONDS
            ):

                continue

            score, reasons, matched_phrases, admin_matched_phrases = (
                score_sermon_moment(
                    thought_text,
                    thought_duration,
                    thought_excitement,
                )
            )

            log()
            log("-" * 70)
            log("COMPLETED THOUGHT")

            log(
                f"Duration: "
                f"{thought_duration:.1f}s"
            )

            log(
                f"Score: {score}"
            )

            if thought_excitement > 1.0:

                log(
                    f"Audio excitement: "
                    f"{thought_excitement:.1f}x baseline"
                )

            log()
            log(thought_text)
            log()

            if reasons:

                log(
                    "Reasons: "
                    +
                    ", ".join(
                        reasons
                    )
                )

            # ---------------------------------------------
            # REMOTE / SCENE-RULE PAUSE
            # ---------------------------------------------

            if remote_state is not None and remote_state.is_paused():

                log(
                    "Status: paused (remote pause active) - not saving."
                )

                continue

            if scene_rule_action == "pause":

                log(
                    "Status: paused (scene rule) - not saving."
                )

                continue

            # ---------------------------------------------
            # SCORE
            # ---------------------------------------------

            if (
                score
                <
                POSSIBLE_THRESHOLD
            ):

                log(
                    "Status: ignore"
                )

                session_stats.record_thought(
                    session_id,
                    "ignored",
                    score,
                    matched_phrases,
                    admin_matched_phrases,
                )

                continue

            if (
                score
                <
                SAVE_THRESHOLD
            ):

                log(
                    "Status: POSSIBLE CLIP"
                )

                session_stats.record_thought(
                    session_id,
                    "possible",
                    score,
                    matched_phrases,
                    admin_matched_phrases,
                )

                continue

            log(
                "Status: STRONG CLIP"
            )

            # ---------------------------------------------
            # COOLDOWN
            # ---------------------------------------------

            elapsed = (
                now
                -
                last_save_time
            )

            if (
                elapsed
                <
                MIN_SECONDS_BETWEEN_SAVES
            ):

                remaining = int(
                    MIN_SECONDS_BETWEEN_SAVES
                    -
                    elapsed
                )

                log(
                    "Cooldown active: "
                    f"{remaining}s remaining."
                )

                # Scored as a real strong clip but throttled by timing,
                # not by score - bucketed as "possible" rather than a
                # dedicated 4th stat, since it's a timing rejection, not
                # a preset-quality one (the thing these stats exist to
                # help tune).
                session_stats.record_thought(
                    session_id,
                    "possible",
                    score,
                    matched_phrases,
                    admin_matched_phrases,
                )

                continue

            # ---------------------------------------------
            # SAVE
            # ---------------------------------------------

            client, clip_number, new_last_save_time, stop_session, saved_clip_path = (
                save_triggered_clip(
                    client,
                    clip_number,
                    score,
                    thought_text,
                    reasons,
                    thought_duration,
                )
            )

            if stop_session:
                session_running = False
                break

            if new_last_save_time is not None:
                last_save_time = new_last_save_time

            if saved_clip_path:
                session_stats.record_saved_clip(session_id, saved_clip_path)
                session_stats.record_thought(
                    session_id,
                    "saved",
                    score,
                    matched_phrases,
                    admin_matched_phrases,
                )

    finally:

        try:

            session_stats.end_session(
                session_id
            )

        except Exception:
            pass

        # If the service ended while our replay buffer
        # was still active, shut it down.
        try:

            stop_auto_replay(
                client,
                auto_started_replay
            )

        except Exception:

            pass

        try:

            stream.stop_stream()
            stream.close()

        except Exception:

            pass

        try:

            audio.terminate()

        except Exception:

            pass

        # Explicitly unload the Small model before
        # Medium is ever launched.
        try:

            del model

        except Exception:

            pass

        gc.collect()

        log()
        log(
            "Live Whisper unloaded."
        )

    return client


# =========================================================
# POST-SERVICE IDLE WAIT
# =========================================================

def wait_post_service_delay():

    log()
    log(
        f"Waiting {POST_SERVICE_DELAY} "
        f"seconds before quality processing..."
    )

    start = time.time()

    client = None

    while True:

        elapsed = (
            time.time()
            -
            start
        )

        remaining = int(
            POST_SERVICE_DELAY
            -
            elapsed
        )

        if remaining <= 0:

            log(
                "Post-service delay complete."
            )

            return True

        # OBS may be closed completely, which is fine.
        if client is None:

            client = try_connect_obs()

        if client is not None:

            status = read_obs_status(
                client
            )

            if not status["connected"]:

                client = None

            elif obs_has_activity(
                status
            ):

                log()
                log(
                    "OBS became active again. "
                    "Cancelling post-service "
                    "processing."
                )

                return False

        time.sleep(
            IDLE_CHECK_INTERVAL
        )


# =========================================================
# MEDIUM MODEL PROCESSOR
# =========================================================

def obs_became_active():

    client = try_connect_obs()

    if client is None:
        return False

    status = read_obs_status(
        client
    )

    if not status["connected"]:
        return False

    return obs_has_activity(
        status
    )


def run_post_service_program(
    role,
    description
):

    log()
    log("=" * 70)
    log(description)
    log("=" * 70)

    command = app_config.worker_launch_command(
        role
    )

    process = subprocess.Popen(
        command,
        cwd=BASE_FOLDER,
        env={
            **os.environ,
            "PYTHONUNBUFFERED": "1",
        },
    )

    while True:

        return_code = (
            process.poll()
        )

        if return_code is not None:

            if return_code == 0:

                log()
                log(
                    f"{description} finished "
                    f"successfully."
                )

                return True

            else:

                log()
                log(
                    f"{description} exited "
                    f"with code {return_code}."
                )

                return False

        # If OBS suddenly starts again,
        # immediately give the GPU back
        # to the live service.
        if obs_became_active():

            log()
            log(
                "OBS became active again."
            )

            log(
                f"Stopping {description}..."
            )

            try:

                process.terminate()

                process.wait(
                    timeout=10
                )

            except Exception:

                try:
                    process.kill()

                except Exception:
                    pass

            return False

        time.sleep(2)


def run_quality_processor():

    log()
    log("=" * 70)
    log(
        "POST-SERVICE PROCESSING STARTING"
    )
    log("=" * 70)

    # =====================================================
    # STAGE 1
    # MEDIUM WHISPER VERIFICATION
    # =====================================================

    if not AUTO_VERIFY:

        log()
        log(
            "Auto Verify is turned off - skipping. "
            "Run Verify Clips manually from the app when you're ready."
        )

        return True

    verified = run_post_service_program(
        "verify",
        "MEDIUM WHISPER VERIFICATION"
    )

    if not verified:

        log()
        log(
            "Verification did not complete."
        )

        return False

    # Make sure OBS has not started again.
    if obs_became_active():

        log()
        log(
            "OBS activity detected before rendering."
        )

        return False

    # =====================================================
    # STAGE 2
    # VERTICAL SHORT RENDERING
    # =====================================================

    if not AUTO_RENDER:

        log()
        log(
            "Auto Render is turned off - skipping. "
            "Run Render Clips manually from the app when you're ready."
        )

        return True

    rendered = run_post_service_program(
        "render",
        "VERTICAL SHORT RENDERING"
    )

    if not rendered:

        log()
        log(
            "Renderer did not complete."
        )

        return False

    # Make sure OBS is still idle before
    # preparing the sermon information.
    if obs_became_active():

        log()
        log(
            "OBS became active before "
            "thumbnail preparation."
        )

        return False

    # =====================================================
    # COMPLETE
    # =====================================================

    log()
    log("=" * 70)
    log(
        "POST-SERVICE PROCESSING COMPLETE"
    )
    log("=" * 70)

    return True
# =========================================================
# CONTROLLER
# =========================================================

def main():

    ensure_folders()

    if not OBS_PASSWORD:

        log(
            "ERROR: OBS_PASSWORD is "
            "missing from .env"
        )

        return

    log()
    log("=" * 70)
    log("SERMON AI CONTROLLER")
    log("=" * 70)

    log(
        f"Started: {datetime.now()}"
    )

    log()
    log(
        "The controller will stay idle "
        "until OBS activity begins."
    )

    remote_state = None

    if REMOTE_API_ENABLED:

        remote_state = remote_api.RemoteControlState()
        server = remote_api.start_server(remote_state, REMOTE_API_PORT)

        if server is not None:

            log()
            log(
                f"Remote API listening on "
                f"http://127.0.0.1:{REMOTE_API_PORT}"
            )

        else:

            log()
            log(
                f"WARNING: Remote API could not bind to port "
                f"{REMOTE_API_PORT} (already in use?) - continuing "
                f"without it."
            )

            remote_state = None

    if remote_state is not None:
        remote_state.update_status(capturing=False, preset=ACTIVE_PRESET.get("label"), clips_saved=0)

    while True:

        try:

            # ---------------------------------------------
            # WAIT FOR CHURCH VIDEO ACTIVITY
            # ---------------------------------------------

            (
                client,
                status,
            ) = wait_for_service_activity()

            # ---------------------------------------------
            # SMALL WHISPER / LIVE CLIPPING
            # ---------------------------------------------

            client = run_live_sermon(
                client,
                status,
                remote_state,
            )

            if remote_state is not None:
                remote_state.update_status(capturing=False)

            # ---------------------------------------------
            # WAIT BEFORE MEDIUM
            # ---------------------------------------------

            safe_to_process = (
                wait_post_service_delay()
            )

            if not safe_to_process:

                log()
                log(
                    "Returning to live "
                    "activity detection."
                )

                continue

            # ---------------------------------------------
            # MEDIUM VERIFICATION
            # ---------------------------------------------

            completed = (
                run_quality_processor()
            )

            if not completed:

                log()
                log(
                    "Quality processing was "
                    "interrupted by new OBS "
                    "activity."
                )

            # ---------------------------------------------
            # RETURN TO IDLE
            # ---------------------------------------------

            log()
            log("=" * 70)
            log(
                "SERMON AI RETURNING TO IDLE"
            )
            log("=" * 70)
            log()

        except KeyboardInterrupt:

            log()
            log(
                "Sermon AI controller stopped."
            )

            break

        except Exception as error:

            log()
            log(
                "Controller encountered "
                "an error:"
            )

            log(
                str(error)
            )

            log(
                "Restarting controller "
                "in 10 seconds..."
            )

            time.sleep(10)


if __name__ == "__main__":
    main()