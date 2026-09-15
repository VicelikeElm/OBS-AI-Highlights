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
}


def get_style(name):
    return RENDER_STYLES.get(name, RENDER_STYLES[DEFAULT_STYLE])


def build_filter_complex(style_key, output_width, output_height, escaped_srt, force_style):
    if style_key == "full_crop":
        video_chain = (
            f"[0:v]scale={output_width}:{output_height}:"
            "force_original_aspect_ratio=increase,"
            f"crop={output_width}:{output_height}"
            "[vertical];"
        )

    elif style_key == "original":
        video_chain = (
            f"[0:v]scale={output_width}:{output_height}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black"
            "[vertical];"
        )

    else:  # blurred_background (default) - the tool's original, unchanged layout
        video_chain = (
            "[0:v]split=2[bg][fg];"
            f"[bg]scale={output_width}:{output_height}:"
            "force_original_aspect_ratio=increase,"
            f"crop={output_width}:{output_height},"
            "gblur=sigma=35"
            "[background];"
            f"[fg]scale={output_width}:{output_height}:"
            "force_original_aspect_ratio=decrease"
            "[foreground];"
            "[background][foreground]overlay=(W-w)/2:(H-h)/2[vertical];"
        )

    return (
        video_chain
        + f"[vertical]subtitles='{escaped_srt}':force_style='{force_style}'[final]"
    )
