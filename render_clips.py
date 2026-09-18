import json
import shutil
import subprocess
import sys
from pathlib import Path

import caption_styles
import config as app_config
import layout_analysis
import presets
import ready_metadata
import render_styles


# =========================================================
# SETTINGS
# =========================================================

CONFIG = app_config.load_config()

SHORTS_ROOT = app_config.get_output_folder(CONFIG)

RAW_FOLDER = SHORTS_ROOT / "Raw"
VERIFIED_FOLDER = SHORTS_ROOT / "Verified"
READY_FOLDER = SHORTS_ROOT / "Ready"

# Final YouTube Short resolution
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920

# Encoding
RENDER_ENCODER = CONFIG.get("render_encoder", app_config.DEFAULTS["render_encoder"])

# NVENC quality setting
NVENC_PRESET = "p5"

# CQ: lower = better quality / larger file
CQ = "20"

AUDIO_BITRATE = "192k"


def _ffmpeg_executable():
    """Path to ffmpeg.exe - the bundled copy build_app_windows.py packages
    next to a frozen install (see its _ensure_bundled_ffmpeg()), if
    present, otherwise plain "ffmpeg" on PATH (dev-mode, or a frozen
    build that somehow shipped without one - falls back instead of
    hard-failing here)."""

    if getattr(sys, "frozen", False):

        bundled = Path(
            getattr(sys, "_MEIPASS", "")
        ) / "ffmpeg.exe"

        if bundled.exists():
            return str(bundled)

    return "ffmpeg"


FFMPEG_EXECUTABLE = _ffmpeg_executable()


def _nvenc_available():
    """A real (but trivial, near-instant) test encode - NVENC can be
    compiled into ffmpeg and still fail at runtime if the GPU driver is
    too old (a real failure mode hit repeatedly on this very machine
    during development: "Driver does not support the required nvenc
    API version"). Just listing encoders wouldn't catch that; only
    actually trying one does.

    256x256 - not something smaller like 64x64 - because NVENC itself
    refuses frames below its own minimum encode dimension ("Frame
    Dimension less than the minimum supported value", confirmed on this
    exact hardware to sit somewhere between 144x144 and 160x160): a
    too-small probe frame produced the exact same failure result as a
    genuinely unavailable/too-old driver, a false negative that wrongly
    sent working NVENC hardware down the slower CPU fallback path."""

    try:
        result = subprocess.run(
            [
                FFMPEG_EXECUTABLE,
                "-f", "lavfi",
                "-i", "color=c=black:s=256x256:d=0.1",
                "-c:v", "h264_nvenc",
                "-frames:v", "1",
                "-f", "null",
                "-",
            ],
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        return result.returncode == 0

    except Exception:
        return False


def _build_encode_args(encoder):
    if encoder == "cpu":
        # libopenh264 - a BSD-licensed software H.264 encoder, no GPU
        # needed, already included in the bundled LGPL ffmpeg build
        # alongside NVENC (see build_app_windows.py). Much slower than
        # hardware encoding, but the only way to render at all on a
        # machine without an NVIDIA GPU. -rc_mode isn't set (its
        # "quality" default already targets these bitrate caps rather
        # than a fixed rate).
        return [
            "-c:v", "libopenh264",
            "-b:v", "6M",
            "-maxrate", "8M",
            "-bufsize", "12M",
        ]

    # NVENC hardware encoding (default).
    return [
        "-c:v", "h264_nvenc",
        "-preset", NVENC_PRESET,
        "-rc", "vbr",
        "-cq", CQ,
        "-b:v", "0",
    ]


# Resolved lazily (on the first actual render, not on import - importing
# this module, e.g. from clip_manager.py for the Clips tab, shouldn't pay
# for a probe subprocess call every time the GUI starts) and cached for
# the rest of this process's lifetime, so a batch of many render jobs in
# one run only probes once. Deliberately never rewrites RENDER_ENCODER
# (the user's saved setting) - a fallback here only affects this
# process's renders, so if the GPU driver gets fixed later, the next
# run naturally tries NVENC again instead of being stuck on CPU forever.
_resolved_encoder = None
_video_encode_args = None


def _get_video_encode_args():
    global _resolved_encoder, _video_encode_args

    if _video_encode_args is not None:
        return _video_encode_args

    _resolved_encoder = RENDER_ENCODER

    if RENDER_ENCODER == "nvenc" and not _nvenc_available():
        print()
        print(
            "WARNING: NVENC isn't available on this machine (no NVIDIA "
            "GPU, or the driver is too old) - falling back to CPU/"
            "software encoding for this render, which will be slower. "
            "Switch 'Render encoder' to CPU / Software in Settings to "
            "skip this check next time."
        )
        _resolved_encoder = "cpu"

    _video_encode_args = _build_encode_args(_resolved_encoder)

    return _video_encode_args


def encoder_in_use():
    """The actual encoder the next render will use - may differ from the
    RENDER_ENCODER setting if NVENC was requested but isn't actually
    available on this machine (see _nvenc_available())."""

    _get_video_encode_args()

    return _resolved_encoder


# =========================================================
# READY-TO-POST METADATA
# =========================================================

ACTIVE_PRESET = presets.get_preset(
    CONFIG.get("preset", presets.DEFAULT_PRESET),
    CONFIG.get("custom_profiles"),
)

# The same phrase lists driving detection are reused to find the
# "strongest" sentence in a clip's own transcript - see ready_metadata.py.
METADATA_PHRASE_LISTS = (
    ACTIVE_PRESET["strong_phrases"],
    ACTIVE_PRESET["application_phrases"],
    ACTIVE_PRESET["reference_phrases"],
)

METADATA_HASHTAGS = ACTIVE_PRESET.get("hashtags", [])


# =========================================================
# CAPTION STYLE
# =========================================================

ACTIVE_CAPTION_STYLE = caption_styles.get_style(
    CONFIG.get("caption_style", caption_styles.DEFAULT_STYLE),
    CONFIG.get("custom_caption_style"),
)

# ASS subtitle styling used by FFmpeg. "clean" (the default) reproduces
# this tool's original hardcoded style exactly - see settings_ui.py's
# Video Style tab for the other built-ins and the custom option.
CAPTION_STYLE = caption_styles.build_force_style(ACTIVE_CAPTION_STYLE)


# =========================================================
# RENDER (VIDEO LAYOUT) STYLE
# =========================================================

RENDER_STYLE_KEY = CONFIG.get("render_style", render_styles.DEFAULT_STYLE)

# Independent on/off switches for the two things this tool otherwise does
# to every clip automatically - so someone who wants to finish a clip
# their own way (their own captions, their own crop, another editor
# entirely) isn't stuck with this tool's opinions baked in. Both default
# True, preserving the only behavior this tool has ever had.
BURN_IN_CAPTIONS = CONFIG.get("burn_in_captions", app_config.DEFAULTS["burn_in_captions"])
APPLY_VERTICAL_LAYOUT = CONFIG.get(
    "apply_vertical_layout", app_config.DEFAULTS["apply_vertical_layout"]
)


# =========================================================
# HELPERS
# =========================================================

def ensure_folders():

    READY_FOLDER.mkdir(
        parents=True,
        exist_ok=True
    )


def find_raw_video(base_name):

    extensions = [
        ".mp4",
        ".mkv",
        ".mov",
        ".m4v",
        ".ts",
    ]

    for extension in extensions:

        path = RAW_FOLDER / (
            base_name + extension
        )

        if path.exists():
            return path

    return None


def already_rendered(base_name):

    output = READY_FOLDER / (
        base_name + "_SHORT.mp4"
    )

    return output.exists()


def escape_subtitle_path(path):

    # FFmpeg's subtitles filter needs Windows paths
    # escaped carefully.

    text = str(
        path.resolve()
    )

    text = text.replace(
        "\\",
        "/"
    )

    text = text.replace(
        ":",
        "\\:"
    )

    text = text.replace(
        "'",
        "\\'"
    )

    return text


def _subtitles_filter(escaped_srt, size):
    """The caption burn-in filter. original_size tells libass the real
    shape of the picture it's drawing on - without it the text is
    stretched by libass's 4:3 caption canvas (see caption_styles.py)."""

    original_size = (
        f":original_size={size[0]}x{size[1]}"
        if size
        else ""
    )

    return (
        f"subtitles='{escaped_srt}'{original_size}:force_style='{CAPTION_STYLE}'"
    )


def build_video_filter(escaped_srt, plan=None, style_key=None, source_size=None):
    """(filter_complex_or_None, map_target) for the ffmpeg command's
    -filter_complex/-map pair, from the independent BURN_IN_CAPTIONS and
    APPLY_VERTICAL_LAYOUT toggles. filter_complex is None when neither is
    enabled - no filtering is needed at all, so the raw video stream maps
    straight through to the encoder untouched (just the trim applied).

    plan is the clip's layout_analysis result, for the styles that need
    one; style_key defaults to the configured RENDER_STYLE_KEY (render_job
    passes the blurred-background fallback when analysis failed);
    source_size is the raw video's (width, height), only used to size the
    captions when the vertical layout is off."""

    if style_key is None:
        style_key = RENDER_STYLE_KEY

    if APPLY_VERTICAL_LAYOUT:

        video_chain = render_styles.build_video_chain(
            style_key,
            OUTPUT_WIDTH,
            OUTPUT_HEIGHT,
            plan,
        )

        if BURN_IN_CAPTIONS:

            filter_complex = (
                video_chain
                + ";[vertical]"
                + _subtitles_filter(escaped_srt, (OUTPUT_WIDTH, OUTPUT_HEIGHT))
                + "[final]"
            )

            return filter_complex, "[final]"

        return video_chain, "[vertical]"

    if BURN_IN_CAPTIONS:

        filter_complex = (
            "[0:v]"
            + _subtitles_filter(escaped_srt, source_size)
            + "[final]"
        )

        return filter_complex, "[final]"

    return None, "0:v"


def plan_layout(raw_video, trim_start, duration):
    """(style_key, plan) to render this clip with. The follow styles look
    at the clip's frames first; if that can't be done (or fails outright),
    the clip is rendered with the default layout, which crops nothing -
    a clip is never skipped or failed over a layout problem."""

    if not APPLY_VERTICAL_LAYOUT or not render_styles.needs_analysis(RENDER_STYLE_KEY):
        return RENDER_STYLE_KEY, None

    plan = None

    try:

        plan = layout_analysis.analyze(
            FFMPEG_EXECUTABLE,
            raw_video,
            trim_start,
            duration,
            want_panel=(RENDER_STYLE_KEY == "stacked"),
        )

    except Exception as error:

        print(f"Layout analysis failed: {error}")

    if plan is None:

        fallback = render_styles.DEFAULT_STYLE

        print(
            f"Layout: could not analyze this clip - using "
            f"{render_styles.get_style(fallback)['label']} instead."
        )

        return fallback, None

    print(
        f"Layout: {render_styles.get_style(RENDER_STYLE_KEY)['label']} "
        f"({layout_analysis.describe(plan)})"
    )

    return RENDER_STYLE_KEY, plan


# =========================================================
# LOAD VERIFIED CLIPS
# =========================================================

def get_verified_jobs():

    jobs = []

    if not VERIFIED_FOLDER.exists():

        return jobs

    for json_path in sorted(
        VERIFIED_FOLDER.glob("*.json")
    ):

        try:

            with open(
                json_path,
                "r",
                encoding="utf-8"
            ) as file:

                metadata = json.load(
                    file
                )

        except Exception as error:

            print(
                f"Could not read "
                f"{json_path.name}: "
                f"{error}"
            )

            continue

        if (
            metadata.get("status")
            !=
            "VERIFIED"
        ):

            continue

        base_name = (
            json_path.stem
        )

        if already_rendered(
            base_name
        ):

            continue

        raw_video = find_raw_video(
            base_name
        )

        if raw_video is None:

            print()
            print(
                "Raw video not found:"
            )

            print(
                base_name
            )

            continue

        srt_path = (
            VERIFIED_FOLDER
            /
            (base_name + ".srt")
        )

        if not srt_path.exists():

            print()
            print(
                "SRT not found:"
            )

            print(
                srt_path
            )

            continue

        trim_start = metadata.get(
            "trim_start"
        )

        trim_end = metadata.get(
            "trim_end"
        )

        if (
            trim_start is None
            or
            trim_end is None
        ):

            print()
            print(
                "Trim information missing:"
            )

            print(
                json_path
            )

            continue

        try:

            trim_start = float(
                trim_start
            )

            trim_end = float(
                trim_end
            )

        except (TypeError, ValueError):

            print(
                "Invalid trim values:"
            )

            print(
                json_path
            )

            continue

        if trim_end <= trim_start:

            print(
                "Invalid trim range:"
            )

            print(
                json_path
            )

            continue

        jobs.append({
            "base_name":
                base_name,

            "raw_video":
                raw_video,

            "srt":
                srt_path,

            "json":
                json_path,

            "trim_start":
                trim_start,

            "trim_end":
                trim_end,

            "transcript":
                metadata.get("verified_matched_transcript")
                or metadata.get("live_transcript", ""),
        })

    return jobs


# =========================================================
# READY-TO-POST METADATA
# =========================================================

def write_ready_metadata(job, base_name):
    """Writes a small sidecar JSON + a human-readable .txt next to the
    finished Short in READY_FOLDER - a suggested title, description,
    hashtags, and the transcript's strongest quote. Deterministic/local
    only (see ready_metadata.py) - never lets a metadata problem fail
    the render itself, since the video is already done at this point."""

    try:

        metadata = ready_metadata.build_metadata(
            job.get(
                "transcript",
                ""
            ),
            METADATA_PHRASE_LISTS,
            METADATA_HASHTAGS,
        )

        json_path = (
            READY_FOLDER
            /
            (base_name + "_SHORT_metadata.json")
        )

        with open(
            json_path,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                metadata,
                file,
                indent=2,
                ensure_ascii=False
            )

        text_path = (
            READY_FOLDER
            /
            (base_name + "_SHORT_metadata.txt")
        )

        with open(
            text_path,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(
                f"Title: {metadata['title']}\n\n"
            )

            file.write(
                f"Description: {metadata['description']}\n\n"
            )

            file.write(
                f"Quote: {metadata['quote']}\n\n"
            )

            file.write(
                "Hashtags: "
                +
                " ".join(
                    metadata["hashtags"]
                )
                +
                "\n"
            )

    except Exception as error:

        print(
            f"WARNING: Could not write ready-to-post metadata: {error}"
        )


# =========================================================
# BUILD VERTICAL VIDEO
# =========================================================

def render_job(job):

    base_name = job[
        "base_name"
    ]

    raw_video = job[
        "raw_video"
    ]

    srt_path = job[
        "srt"
    ]

    trim_start = job[
        "trim_start"
    ]

    trim_end = job[
        "trim_end"
    ]

    duration = (
        trim_end
        -
        trim_start
    )

    output_path = (
        READY_FOLDER
        /
        (
            base_name
            +
            "_SHORT.mp4"
        )
    )

    escaped_srt = (
        escape_subtitle_path(
            srt_path
        )
    )

    print()
    print("=" * 70)
    print("RENDERING SHORT")
    print("=" * 70)

    print()
    print(
        f"Source: "
        f"{raw_video.name}"
    )

    print(
        f"Start: "
        f"{trim_start:.2f}s"
    )

    print(
        f"End: "
        f"{trim_end:.2f}s"
    )

    print(
        f"Duration: "
        f"{duration:.2f}s"
    )

    print()

    # -----------------------------------------------------
    # FILTER DESIGN
    # -----------------------------------------------------
    #
    # The actual filter graph is built by render_styles.py, keyed off
    # the configured RENDER_STYLE_KEY - see that module for the layouts
    # (blurred background, full crop, original/letterboxed, or the two
    # follow layouts, which plan_layout() analyzes the clip for first).
    # Both the vertical layout and the caption burn-in are independently
    # optional (BURN_IN_CAPTIONS / APPLY_VERTICAL_LAYOUT) - see
    # build_video_filter().

    style_key, plan = plan_layout(
        raw_video,
        trim_start,
        duration
    )

    source_size = None

    if BURN_IN_CAPTIONS and not APPLY_VERTICAL_LAYOUT:

        source_size = layout_analysis.probe_video_size(
            FFMPEG_EXECUTABLE,
            raw_video
        )

    filter_complex, video_map_target = build_video_filter(
        escaped_srt,
        plan,
        style_key,
        source_size,
    )

    command = [
        FFMPEG_EXECUTABLE,

        "-y",

        # Seek before decoding.
        "-ss",
        f"{trim_start:.3f}",

        "-i",
        str(raw_video),

        "-t",
        f"{duration:.3f}",
    ]

    if filter_complex:

        command += [
            "-filter_complex",
            filter_complex,
        ]

    command += [
        "-map",
        video_map_target,

        "-map",
        "0:a?",

        # -----------------------------------------------
        # VIDEO ENCODER (NVENC or CPU/libopenh264 - see RENDER_ENCODER)
        # -----------------------------------------------

        *_get_video_encode_args(),

        # YouTube-friendly pixel format
        "-pix_fmt",
        "yuv420p",

        # -----------------------------------------------
        # AUDIO
        # -----------------------------------------------

        "-c:a",
        "aac",

        "-b:a",
        AUDIO_BITRATE,

        "-ar",
        "48000",

        # -----------------------------------------------
        # MP4
        # -----------------------------------------------

        "-movflags",
        "+faststart",

        str(
            output_path
        ),
    ]

    print(
        "Starting FFmpeg..."
    )

    print()

    result = subprocess.run(
        command,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    if result.returncode != 0:

        print()
        print(
            "ERROR: FFmpeg failed."
        )

        return False

    if not output_path.exists():

        print()
        print(
            "ERROR: Output video "
            "was not created."
        )

        return False

    write_ready_metadata(
        job,
        base_name
    )

    size_mb = (
        output_path.stat().st_size
        /
        1024
        /
        1024
    )

    print()
    print(
        "SHORT COMPLETE"
    )

    print(
        output_path
    )

    print(
        f"File size: "
        f"{size_mb:.1f} MB"
    )

    return True


# =========================================================
# MAIN
# =========================================================

def main():

    ensure_folders()

    print()
    print("=" * 70)
    print(
        "SERMON AI - "
        "SHORT RENDERER"
    )
    print("=" * 70)

    if FFMPEG_EXECUTABLE == "ffmpeg" and shutil.which("ffmpeg") is None:

        print()
        print(
            "ERROR: ffmpeg was not found."
        )
        print(
            "Install it from https://ffmpeg.org/download.html "
            "and make sure ffmpeg.exe is on your PATH, then try again."
        )

        return

    jobs = get_verified_jobs()

    if not jobs:

        print()
        print(
            "No new VERIFIED clips "
            "need rendering."
        )

        return

    print()
    print(
        f"Found "
        f"{len(jobs)} "
        f"Short(s) to render."
    )

    completed = 0
    failed = 0

    for job in jobs:

        try:

            success = render_job(
                job
            )

            if success:
                completed += 1

            else:
                failed += 1

        except KeyboardInterrupt:

            print()
            print(
                "Rendering stopped."
            )

            return

        except Exception as error:

            failed += 1

            print()
            print(
                "ERROR rendering:"
            )

            print(
                job["base_name"]
            )

            print(
                error
            )

    print()
    print("=" * 70)

    print(
        f"Finished: "
        f"{completed} rendered, "
        f"{failed} failed."
    )

    print("=" * 70)


if __name__ == "__main__":
    main()