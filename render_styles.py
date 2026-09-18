# -*- coding: utf-8 -*-
"""Built-in vertical-video render layouts for render_clips.py.

The capture pipeline saves OBS's single composited program output (the
replay buffer), not separate source tracks - so a style that would need
independent face-cam/gameplay sources (Face Cam + Gameplay, Top/Bottom)
isn't implemented here. That needs OBS recording those as separate
tracks in the first place, a bigger capture-side change, not just a
render option. Deferred.

"blurred_background" (the default) reproduces this tool's original,
only-ever layout exactly: the complete original frame, scaled down and
centered over a blurred, cropped-to-fill copy of itself as the
background - nothing is ever cropped away.

"follow_crop" and "stacked" (ANALYZED_STYLES) crop, but follow the speaker
instead of cutting a fixed center slice. They need render_clips.py to
analyze the clip first (layout_analysis.py) and pass the result to
build_video_chain as `plan`.
"""

DEFAULT_STYLE = "blurred_background"

RENDER_STYLES = {
    "blurred_background": {
        "label": "Blurred Background",
        "description": "Full original frame, centered over a blurred copy of itself. Nothing is cropped.",
    },
    "full_crop": {
        "label": "Full Crop",
        "description": "Scaled and cropped to fill the entire vertical frame. Fills the screen, crops the sides.",
    },
    "original": {
        "label": "Original (letterboxed)",
        "description": "Original aspect ratio kept intact with black bars filling the rest.",
    },
    "follow_crop": {
        "label": "Follow Speaker Crop",
        "description": (
            "Full-screen crop that follows the speaker as they move, instead of "
            "cutting a fixed slice from the middle. Crops the sides. Falls back to "
            "Blurred Background if the speaker can't be found."
        ),
    },
    "stacked": {
        "label": "Speaker + Lower Panel",
        "description": (
            "Speaker on top with the scene's lower panel (scripture, lyrics, lower "
            "third) underneath while it's on screen; a full-screen follow crop the "
            "rest of the time. Falls back to Blurred Background if the clip can't "
            "be analyzed."
        ),
    },
}

# These layouts need render_clips.py to look at the clip's frames first
# (layout_analysis.py) and pass the result to build_video_chain as `plan`.
ANALYZED_STYLES = ("follow_crop", "stacked")

# The stacked layout, as fractions of the output height: the speaker block
# is a square (as wide as the frame) that starts this far down, the panel
# sits directly under it, and captions live in the space below the panel.
STACK_TOP_FRACTION = 120 / 1920


def get_style(name):
    return RENDER_STYLES.get(name, RENDER_STYLES[DEFAULT_STYLE])


def needs_analysis(style_key):
    return style_key in ANALYZED_STYLES


def _center_expression(plan):
    """Where the crop is pointed (0-1 across the frame) as a function of
    time t: the starting spot plus one clipped ramp per pan."""

    text = f"{plan['start_center']:.4f}"

    for second, change, length in plan["moves"]:
        text += (
            f"{change:+.4f}"
            f"*clip((t-{second:.2f})/{length:.2f},0,1)"
        )

    return f"({text})"


def _follow_x(plan):
    # Left edge of a crop centred on the speaker, kept inside the frame.
    return f"clip({_center_expression(plan)}*iw-out_w/2,0,iw-out_w)"


def _follow_crop_filter(output_width, output_height, plan):
    """A 9:16 slice at full height that follows the speaker, scaled to the
    output size. Sizes are expressions of the input (iw/ih), so any
    source resolution works."""

    return (
        f"crop=w='trunc(ih*{output_width}/{output_height}/2)*2':h=ih:"
        f"x='{_follow_x(plan)}':y=0,"
        f"scale={output_width}:{output_height}:flags=lanczos"
    )


def _stacked_chain(output_width, output_height, plan):
    top = round(output_height * STACK_TOP_FRACTION)

    # Even pixel row where the panel starts, from its fraction of the
    # source height.
    panel_row = f"trunc(ih*{plan['panel_top']:.4f}/2)*2"

    enable = "+".join(
        f"between(t,{start:.2f},{end:.2f})"
        for start, end in plan["panel_segments"]
    )

    blur_width = output_width // 10
    blur_height = output_height // 10

    return (
        "[0:v]split=3[camfull][camsquare][campanel];"

        f"[camfull]{_follow_crop_filter(output_width, output_height, plan)}"
        "[full];"

        # The speaker block: a square of the camera above the panel.
        f"[camsquare]crop=w='{panel_row}':h='{panel_row}':"
        f"x='{_follow_x(plan)}':y=0,"
        f"scale={output_width}:{output_width}:flags=lanczos,"
        "split=2[speakerblock][blurin];"

        # Backdrop: the speaker square blurred to fill the screen, cheaply
        # at low resolution, and dimmed. Taken from the square, not the
        # full crop, so no panel text bleeds into it. The dimming is
        # lutyuv, not eq: the bundled ffmpeg is an LGPL build, and eq is
        # GPL-only, so it isn't in there (build_app_windows.py).
        "[blurin]"
        f"scale={blur_width}:{blur_height}:"
        "force_original_aspect_ratio=increase:flags=area,"
        f"crop={blur_width}:{blur_height},"
        "gblur=sigma=4,"
        "lutyuv=y='val*0.88',"
        f"scale={output_width}:{output_height}:flags=bicubic"
        "[stackbg];"

        # The panel itself, at full width.
        f"[campanel]crop=w=iw:h='ih-{panel_row}':x=0:y='{panel_row}',"
        f"scale={output_width}:-2:flags=lanczos"
        "[panelblock];"

        f"[stackbg][speakerblock]overlay=0:{top}[stackmid];"

        f"[stackmid][panelblock]overlay=0:{top + output_width}[stacked];"

        # The stacked layout covers the full-screen crop only while the
        # panel is up.
        "[full][stacked]"
        f"overlay=0:0:enable='{enable}'"
        "[vertical]"
    )


def build_video_chain(style_key, output_width, output_height, plan=None):
    """The layout-only filter chain for style_key (scale/crop/blur/pad,
    as appropriate) - starts from the raw "[0:v]" input and ends in an
    unterminated "[vertical]" label, no trailing ";" and no captions.
    render_clips.py decides what (if anything) comes next - burning in
    captions, or using "[vertical]" as the map target directly - since
    both the vertical layout and the caption burn-in are independently
    optional (see its BURN_IN_CAPTIONS/APPLY_VERTICAL_LAYOUT).

    plan is layout_analysis.analyze()'s result, needed only by the styles
    in ANALYZED_STYLES; without one they use the default layout."""
    if needs_analysis(style_key):

        if plan is None:
            return build_video_chain(DEFAULT_STYLE, output_width, output_height)

        if style_key == "stacked" and plan["panel_segments"]:
            return _stacked_chain(output_width, output_height, plan)

        return (
            f"[0:v]{_follow_crop_filter(output_width, output_height, plan)}"
            "[vertical]"
        )

    if style_key == "full_crop":
        return (
            f"[0:v]scale={output_width}:{output_height}:"
            "force_original_aspect_ratio=increase,"
            f"crop={output_width}:{output_height}"
            "[vertical]"
        )

    if style_key == "original":
        return (
            f"[0:v]scale={output_width}:{output_height}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black"
            "[vertical]"
        )

    # blurred_background (default) - the tool's original, unchanged layout
    return (
        "[0:v]split=2[bg][fg];"
        f"[bg]scale={output_width}:{output_height}:"
        "force_original_aspect_ratio=increase,"
        f"crop={output_width}:{output_height},"
        "gblur=sigma=35"
        "[background];"
        f"[fg]scale={output_width}:{output_height}:"
        "force_original_aspect_ratio=decrease"
        "[foreground];"
        "[background][foreground]overlay=(W-w)/2:(H-h)/2[vertical]"
    )
