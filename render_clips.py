import json
import shutil
import subprocess
import sys
from pathlib import Path

import caption_styles
import config as app_config
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
VIDEO_CODEC = "h264_nvenc"

# RTX 2070 NVENC quality setting
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
    # (blurred background, full crop, or original/letterboxed).

    filter_complex = render_styles.build_filter_complex(
        RENDER_STYLE_KEY,
        OUTPUT_WIDTH,
        OUTPUT_HEIGHT,
        escaped_srt,
        CAPTION_STYLE,
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

        "-filter_complex",
        filter_complex,

        "-map",
        "[final]",

        "-map",
        "0:a?",

        # -----------------------------------------------
        # RTX 2070 NVENC
        # -----------------------------------------------

        "-c:v",
        VIDEO_CODEC,

        "-preset",
        NVENC_PRESET,

        "-rc",
        "vbr",

        "-cq",
        CQ,

        "-b:v",
        "0",

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
        command
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