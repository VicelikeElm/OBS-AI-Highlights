# -*- coding: utf-8 -*-
"""Light/dark theme support.

sv_ttk themes every ttk widget automatically and instantly on
set_theme() - but a handful of plain tk widgets (Text, mainly) hardcode
their own colors and sv_ttk has no way to reach them. This module is
the single place that knows both the saved preference and the plain-tk
color pairs, so app.py and settings_ui.py stay in sync without either
one hardcoding a palette of its own.
"""

import config as app_config

DEFAULT_THEME = app_config.DEFAULTS["theme"]

THEME_LABELS = {
    "dark": "Dark",
    "light": "Light",
}
THEME_KEY_BY_LABEL = {v: k for k, v in THEME_LABELS.items()}

# Only for plain tk widgets ttk/sv_ttk can't reach (see module docstring).
# Not used for the caption style preview canvas - that one simulates a
# video frame's own background, not the app's chrome, and stays dark
# regardless of theme.
TEXT_WIDGET_COLORS = {
    "dark": {"background": "#111111", "foreground": "#DDDDDD"},
    "light": {"background": "#FFFFFF", "foreground": "#111111"},
}


def get_theme(config=None):
    config = config if config is not None else app_config.load_config()
    theme = config.get("theme", DEFAULT_THEME)
    return theme if theme in TEXT_WIDGET_COLORS else DEFAULT_THEME


def text_widget_colors(theme):
    return TEXT_WIDGET_COLORS.get(theme, TEXT_WIDGET_COLORS[DEFAULT_THEME])


def apply_theme(theme):
    """Switches every ttk widget in the whole app instantly. Safe to call
    even if sv_ttk failed to import or the theme name is unrecognized -
    a broken theme switch should never take the app down with it."""
    try:
        import sv_ttk
    except Exception:
        return

    try:
        sv_ttk.set_theme(theme)
    except Exception:
        pass
