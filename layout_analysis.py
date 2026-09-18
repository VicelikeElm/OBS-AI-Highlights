# -*- coding: utf-8 -*-
"""Frame analysis behind the "follow" render layouts (see render_styles.py).

A fixed center crop cuts the speaker off whenever they aren't standing in
the middle of the frame, and a fixed layout can't know whether a scene
has a lower panel (scripture, lyrics, a lower third) on screen. So the
follow layouts look at each clip's own frames first:

  - where the main subject is, from where things move (the camera is
    static, the speaker is what moves), and how that changes over time
  - when a full-width lower panel is on screen, from the horizontal edge
    it draws across the frame

Everything is reported as a fraction of the frame (0-1), not pixels, so
the same plan fits a 1080p, 1440p or 4K recording. numpy is the only
extra dependency (already installed with faster-whisper). Every function
here fails soft - analyze() returns None whenever it can't produce a
trustworthy plan, and render_clips.py then falls back to the Blurred
Background layout, which never crops anything.
"""

import re
import subprocess

try:
    import numpy
except ImportError:
    numpy = None


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Frames are sampled small and often enough to follow the speaker and
# catch a panel appearing. The height follows the source's aspect ratio.
ANALYSIS_FPS = 2
ANALYSIS_WIDTH = 384

# A lower panel is a full-width band that starts about halfway down the
# scene. It shows up as a horizontal edge spanning most of the frame,
# which nothing else on a stage does.
PANEL_SEARCH_TOP = 0.45
PANEL_SEARCH_BOTTOM = 0.72
PANEL_EDGE_STEP = 18
PANEL_EDGE_COVERAGE = 0.55

# Ignore panel blips shorter than this, and bridge gaps shorter than
# this, so the layout never flickers back and forth.
PANEL_MIN_SECONDS = 2.5
PANEL_MERGE_SECONDS = 3.0

# The speaker is found by where things move. The crop follows only once
# they have stayed this far (as a fraction of frame width) off from where
# the crop already points for PAN_HOLD_SECONDS, then glides over.
MOTION_STEP = 22
MOTION_GLOBAL_LIMIT = 0.30
TRACK_WINDOW_SECONDS = 6.0
TRACK_MIN_MOTION = 60
PAN_DEADBAND = 0.09
PAN_SPEED = 0.16
PAN_MIN_SECONDS = 1.2
PAN_START_SECONDS = 10.0
PAN_HOLD_SECONDS = 4.0


def probe_video_size(ffmpeg, video_path):
    """(width, height) of the first video stream, read from ffmpeg's own
    banner - the bundled build has no ffprobe. None if it can't be read."""

    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(video_path)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )

    except Exception:
        return None

    match = re.search(
        r"Video:.*?(?<!\w)(\d{2,5})x(\d{2,5})(?!\w)",
        result.stderr
    )

    if match is None:
        return None

    return int(match.group(1)), int(match.group(2))


def sample_frames(ffmpeg, video_path, start, duration, size):
    """Small RGB frames of just the part of the clip that becomes the
    Short, as an array shaped (frames, height, width, 3)."""

    source_width, source_height = size

    height = max(
        2,
        round(ANALYSIS_WIDTH * source_height / source_width / 2) * 2
    )

    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-v", "error",
                "-ss", f"{start:.3f}",
                "-t", f"{duration:.3f}",
                "-i", str(video_path),
                "-an",
                "-vf",
                (
                    f"fps={ANALYSIS_FPS},"
                    f"scale={ANALYSIS_WIDTH}:{height}:flags=area"
                ),
                "-pix_fmt", "rgb24",
                "-f", "rawvideo",
                "-",
            ],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        )

    except Exception:
        return None

    frame_bytes = ANALYSIS_WIDTH * height * 3

    count = len(result.stdout) // frame_bytes

    if count == 0:
        return None

    return numpy.frombuffer(
        result.stdout[:count * frame_bytes],
        dtype=numpy.uint8
    ).reshape(
        count,
        height,
        ANALYSIS_WIDTH,
        3
    )


def detect_panel(frames):
    """Per frame: whether a lower panel is up, and where its top edge is
    (0-1 down the frame, None if no edge was found). The edge is a row
    where most columns change sharply from one side of the row to the
    other."""

    height = frames.shape[1]

    low = int(height * PANEL_SEARCH_TOP)
    high = int(height * PANEL_SEARCH_BOTTOM)

    present = []
    tops = []

    for frame in frames:

        band = frame[low - 2:high + 2].astype(numpy.int16)

        change = numpy.abs(
            band[4:] - band[:-4]
        ).max(axis=2)

        coverage = (
            change > PANEL_EDGE_STEP
        ).mean(axis=1)

        rows = numpy.nonzero(
            coverage >= PANEL_EDGE_COVERAGE
        )[0]

        present.append(len(rows) > 0)

        tops.append(
            (low + int(rows[0])) / height
            if len(rows) > 0
            else None
        )

    return present, tops


def panel_segments(present):
    """Per-frame flags -> (start, end) seconds. Short gaps are bridged and
    short appearances dropped so the layout doesn't flicker."""

    runs = []

    for index, flag in enumerate(present):

        if not flag:
            continue

        if runs and runs[-1][1] == index:
            runs[-1][1] = index + 1

        else:
            runs.append([index, index + 1])

    merged = []

    merge_gap = PANEL_MERGE_SECONDS * ANALYSIS_FPS

    for run in runs:

        if merged and run[0] - merged[-1][1] < merge_gap:
            merged[-1][1] = run[1]

        else:
            merged.append(run)

    min_length = PANEL_MIN_SECONDS * ANALYSIS_FPS

    segments = []

    for start, end in merged:

        if end - start < min_length:
            continue

        # Start a touch early: a panel fades in, and the layout should
        # already be there.
        segments.append((
            max(0.0, start / ANALYSIS_FPS - 0.25),
            end / ANALYSIS_FPS + 0.25,
        ))

    return segments


def track_subject(frames, present, panel_top):
    """The subject's horizontal position per frame (0-1 across the frame),
    from where things move, ignoring the lower panel (it changes
    constantly). None when there's no usable motion at all."""

    count, height, width, _ = frames.shape

    motion = numpy.zeros((count, width))

    panel_row = height

    if panel_top is not None:
        panel_row = int((panel_top - 0.02) * height)

    for index in range(1, count):

        moved = (
            numpy.abs(
                frames[index].astype(numpy.int16)
                - frames[index - 1].astype(numpy.int16)
            ).max(axis=2)
            > MOTION_STEP
        )

        if present[index] or present[index - 1]:
            moved[panel_row:] = False

        # A camera move or scene cut lights up the whole frame. That says
        # nothing about the subject.
        if moved.mean() > MOTION_GLOBAL_LIMIT:
            continue

        motion[index] = moved.sum(axis=0)

    half = int(TRACK_WINDOW_SECONDS * ANALYSIS_FPS)

    columns = numpy.arange(width)

    smooth = numpy.ones(15) / 15

    positions = []

    for index in range(count):

        window = motion[
            max(0, index - half):index + half + 1
        ].sum(axis=0)

        if window.sum() < TRACK_MIN_MOTION:
            positions.append(None)
            continue

        # Centre on the busiest stretch of columns so a stray bit of
        # motion far away can't drag the crop off the subject.
        peak = int(
            numpy.convolve(window, smooth, mode="same").argmax()
        )

        near = numpy.abs(columns - peak) <= (width // 8)

        positions.append(
            float(
                (columns[near] * window[near]).sum()
                / window[near].sum()
            ) / width
        )

    known = [p for p in positions if p is not None]

    if not known:
        return None

    # Frames with no motion keep the nearest known position.
    last = known[0]

    filled = []

    for position in positions:

        if position is not None:
            last = position

        filled.append(last)

    return filled


def plan_pan(centers, crop_fraction):
    """Camera-operator style: hold still, and only move once the subject
    has stayed well off-centre for a while, then glide there. Returns the
    starting centre (0-1) and the moves as (start second, change, seconds
    to take)."""

    low = crop_fraction / 2
    high = 1 - crop_fraction / 2

    targets = [
        min(high, max(low, center))
        for center in centers
    ]

    # Start where they stand for the first stretch of the clip, not
    # wherever one noisy frame put them.
    opening = sorted(
        targets[:max(1, int(PAN_START_SECONDS * ANALYSIS_FPS))]
    )

    current = opening[len(opening) // 2]

    start = current

    moves = []

    busy_until = 0.0

    away = []

    hold = int(PAN_HOLD_SECONDS * ANALYSIS_FPS)

    for index, target in enumerate(targets):

        second = index / ANALYSIS_FPS

        if second < busy_until:
            continue

        # A gesture or a step to the side isn't a move. They have to stay
        # off-centre for a few seconds first.
        if abs(target - current) <= PAN_DEADBAND:
            away = []
            continue

        away.append(target)

        if len(away) < hold:
            continue

        destination = sorted(away)[len(away) // 2]

        length = max(
            PAN_MIN_SECONDS,
            abs(destination - current) / PAN_SPEED
        )

        moves.append((
            second,
            destination - current,
            length
        ))

        current = destination

        away = []

        busy_until = second + length

    return start, moves


def analyze(ffmpeg, video_path, trim_start, duration, want_panel):
    """How to frame one Short, or None when the clip can't be analyzed
    (the caller falls back to a layout that crops nothing).

    A plan is {"start_center", "moves", "panel_top", "panel_segments"}:
    the crop's starting centre and pans, and - when want_panel is set and
    a lower panel is on screen for a while - where the panel starts (0-1
    down the frame) and the (start, end) seconds it's up for."""

    if numpy is None:
        print("Layout analysis unavailable (numpy is missing).")
        return None

    size = probe_video_size(ffmpeg, video_path)

    if size is None:
        print("Layout analysis: could not read the video size.")
        return None

    frames = sample_frames(
        ffmpeg,
        video_path,
        trim_start,
        duration,
        size
    )

    if frames is None or len(frames) < 4:
        print("Layout analysis: could not sample the clip's frames.")
        return None

    present, tops = detect_panel(frames)

    known_tops = [top for top in tops if top is not None]

    panel_top = min(known_tops) if known_tops else None

    centers = track_subject(frames, present, panel_top)

    if centers is None:
        print("Layout analysis: could not find the speaker (no motion).")
        return None

    # A 9:16 slice at full height, as a fraction of the frame's width.
    source_width, source_height = size

    crop_fraction = min(1.0, (9 / 16) * source_height / source_width)

    start_center, moves = plan_pan(centers, crop_fraction)

    segments = panel_segments(present) if want_panel else []

    return {
        "start_center": start_center,
        "moves": moves,
        "panel_top": (
            # A hair in, so the speaker block never shows a sliver of the
            # panel's top edge.
            min(0.95, panel_top + 0.006)
            if segments
            else None
        ),
        "panel_segments": segments,
    }


def describe(plan):
    """One line for the render log."""

    if plan is None:
        return "no analysis"

    text = f"follows the speaker, {len(plan['moves'])} pan(s)"

    if plan["panel_segments"]:

        spans = ", ".join(
            f"{start:.1f}-{end:.1f}s"
            for start, end in plan["panel_segments"]
        )

        text += f"; lower panel stacked at {spans}"

    return text
