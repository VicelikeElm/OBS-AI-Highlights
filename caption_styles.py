# -*- coding: utf-8 -*-
"""Built-in caption/burn-in styles for render_clips.py, plus the one
"custom" slot a user can tune themselves (mirrors presets.py's shape,
but caption styles aren't named/multi-profile like content presets -
just a handful of built-ins and one custom style).

Fields are plain-language (hex colors, percent opacity, named position)
rather than raw ASS syntax, so a settings UI never has to expose ASS's
inverted-alpha BGR hex format directly. build_force_style() converts a
style dict into the ffmpeg subtitles=...:force_style='...' value.

Word-by-word karaoke highlighting is NOT implemented here - it would
need per-word ASS \\k tags written into an .ass file instead of a plain
.srt, a bigger change to verify_clips.py's caption output, not just a
style knob. Deferred.
"""

DEFAULT_STYLE = "clean"

POSITION_ALIGNMENT = {
    "bottom": 2,
    "middle": 5,
    "top": 8,
}

CAPTION_STYLES = {
    "clean": {
        "label": "Clean",
        "font_name": "Arial",
        "font_size": 24,
        "bold": True,
        "text_color": "#FFFFFF",
        "outline_color": "#000000",
        "outline_width": 3,
        "shadow": 1,
        "background_enabled": False,
        "background_color": "#000000",
        "background_opacity": 50,
        "position": "bottom",
        "margin_h": 90,
        "margin_v": 300,
        "max_words_per_line": 7,
    },
    "mrbeast": {
        "label": "MrBeast-ish",
        "font_name": "Arial Black",
        "font_size": 34,
        "bold": True,
        "text_color": "#FFFF00",
        "outline_color": "#000000",
        "outline_width": 4,
        "shadow": 0,
        "background_enabled": False,
        "background_color": "#000000",
        "background_opacity": 60,
        "position": "bottom",
        "margin_h": 60,
        "margin_v": 260,
        "max_words_per_line": 3,
    },
    "church": {
        "label": "Church",
        "font_name": "Georgia",
        "font_size": 26,
        "bold": False,
        "text_color": "#FFFFFF",
        "outline_color": "#000000",
        "outline_width": 2,
        "shadow": 1,
        "background_enabled": True,
        "background_color": "#000000",
        "background_opacity": 45,
        "position": "bottom",
        "margin_h": 100,
        "margin_v": 280,
        "max_words_per_line": 6,
    },
    "gaming": {
        "label": "Gaming",
        "font_name": "Arial Black",
        "font_size": 30,
        "bold": True,
        "text_color": "#00FFAA",
        "outline_color": "#000000",
        "outline_width": 3,
        "shadow": 0,
        "background_enabled": False,
        "background_color": "#000000",
        "background_opacity": 60,
        "position": "bottom",
        "margin_h": 70,
        "margin_v": 260,
        "max_words_per_line": 5,
    },
}


def blank_custom_style():
    """Starting point for the "custom" slot - a copy of Clean, not an
    empty dict. Unlike a blank phrase-list preset (empty lists are a
    valid, harmless starting point), an empty caption style would mean
    FontSize=0 and other nonsense values reaching ffmpeg."""
    return dict(CAPTION_STYLES[DEFAULT_STYLE])


def get_style(name, custom_override=None):
    """Return the style dict for `name`. "custom" merges custom_override
    over blank_custom_style(). Unknown names fall back to DEFAULT_STYLE
    rather than raising, since this is read on every render and a bad or
    missing config value shouldn't crash the pipeline."""
    if name == "custom":
        merged = blank_custom_style()
        if custom_override:
            merged.update(custom_override)
        merged["label"] = "Custom"
        return merged

    return CAPTION_STYLES.get(name, CAPTION_STYLES[DEFAULT_STYLE])


def hex_to_ass_color(hex_color, opacity=100):
    """"#RRGGBB" + opacity percent (0=fully transparent, 100=opaque) ->
    ASS's "&HAABBGGRR" format - color channels reversed (BGR, not RGB)
    and alpha inverted (00=opaque, FF=transparent), both standard ASS/
    libass quirks, not something a settings UI should need to know."""
    hex_color = str(hex_color).lstrip("#")
    if len(hex_color) != 6:
        hex_color = "FFFFFF"

    r, g, b = hex_color[0:2], hex_color[2:4], hex_color[4:6]

    opacity = max(0, min(100, opacity))
    alpha = round((100 - opacity) / 100 * 255)

    return f"&H{alpha:02X}{b.upper()}{g.upper()}{r.upper()}"


def build_force_style(style):
    background_enabled = style.get("background_enabled", False)

    return (
        f"FontName={style.get('font_name', 'Arial')},"
        f"FontSize={style.get('font_size', 24)},"
        f"Bold={1 if style.get('bold', True) else 0},"
        f"PrimaryColour={hex_to_ass_color(style.get('text_color', '#FFFFFF'))},"
        f"OutlineColour={hex_to_ass_color(style.get('outline_color', '#000000'))},"
        f"BackColour={hex_to_ass_color(style.get('background_color', '#000000'), style.get('background_opacity', 50))},"
        f"BorderStyle={3 if background_enabled else 1},"
        f"Outline={style.get('outline_width', 3)},"
        f"Shadow={style.get('shadow', 1)},"
        f"Alignment={POSITION_ALIGNMENT.get(style.get('position', 'bottom'), 2)},"
        f"MarginL={style.get('margin_h', 90)},"
        f"MarginR={style.get('margin_h', 90)},"
        f"MarginV={style.get('margin_v', 300)}"
    )
