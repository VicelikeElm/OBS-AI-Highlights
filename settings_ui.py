# -*- coding: utf-8 -*-
"""Standalone settings app for the OBS AI Highlights tool.

Own Tk root, own main() - matches the standalone-app shape used
throughout the Sunday Service System (e.g. ProfileManager in
sss_profile_manager.py) but this tool has no dependency on that
codebase at all. Run directly:

    python settings_ui.py
"""

import json
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

import caption_styles
import config as app_config
import presets
import render_styles
import ui_theme


def _enumerate_loopback_devices():
    """Real WASAPI loopback-capable device names on this machine (what
    highlight_engine.py actually needs - capturing what's playing OUT of
    a speaker/headphone device, not a microphone). Returns [] on any
    failure (no audio hardware, PortAudio not available, etc.) so the
    Settings UI can still fall back to manual entry rather than crash."""
    try:
        import pyaudiowpatch as pyaudio
    except Exception:
        return []

    try:
        audio = pyaudio.PyAudio()
    except Exception:
        return []

    try:
        names = []
        for info in audio.get_loopback_device_info_generator():
            name = str(info.get("name", "")).strip()
            if name:
                names.append(name)
        return names
    except Exception:
        return []
    finally:
        audio.terminate()


def _enumerate_input_devices():
    """Real microphone/input device names on this machine - anything
    input-capable that ISN'T a WASAPI loopback endpoint. Returns [] on
    any failure, same fallback behavior as _enumerate_loopback_devices()."""
    try:
        import pyaudiowpatch as pyaudio
    except Exception:
        return []

    try:
        audio = pyaudio.PyAudio()
    except Exception:
        return []

    try:
        names = []
        for index in range(audio.get_device_count()):
            try:
                info = audio.get_device_info_by_index(index)
            except Exception:
                continue
            if info.get("maxInputChannels", 0) <= 0:
                continue
            if info.get("isLoopbackDevice", False):
                continue
            name = str(info.get("name", "")).strip()
            if name:
                names.append(name)
        return names
    except Exception:
        return []
    finally:
        audio.terminate()


WHISPER_MODEL_OPTIONS = (
    "tiny",
    "tiny.en",
    "base",
    "base.en",
    "small",
    "small.en",
    "medium",
    "medium.en",
    "large-v2",
    "large-v3",
    "large-v3-turbo",
)

DEVICE_OPTIONS = ("cuda", "cpu", "auto")

COMPUTE_TYPE_OPTIONS = (
    "float32",
    "float16",
    "bfloat16",
    "int8",
    "int8_float16",
    "int8_float32",
    "int8_bfloat16",
)

SCENE_ACTION_LABELS = {
    "pause": "Pause clipping",
    "ignore": "Ignore entirely",
    "preset": "Switch preset",
}
SCENE_ACTION_KEY_BY_LABEL = {v: k for k, v in SCENE_ACTION_LABELS.items()}

RENDER_ENCODER_LABELS = {
    "nvenc": "NVENC (GPU, fastest)",
    "cpu": "CPU / Software (no GPU needed, slower)",
}
RENDER_ENCODER_KEY_BY_LABEL = {v: k for k, v in RENDER_ENCODER_LABELS.items()}

AUDIO_SOURCE_TYPE_LABELS = {
    "loopback": "Loopback (what OBS sends to output)",
    "microphone": "Microphone (direct input)",
}
AUDIO_SOURCE_TYPE_KEY_BY_LABEL = {v: k for k, v in AUDIO_SOURCE_TYPE_LABELS.items()}

PHRASE_LIST_FIELDS = (
    ("strong_phrases", "Strong phrases (one per line)"),
    ("application_phrases", "Application / call-to-action phrases"),
    ("reference_phrases", "Reference phrases (Scripture, game terms, etc.)"),
    ("low_value_phrases", "Low-value phrases (penalize the score)"),
    ("pause_phrases", "Pause phrases (suspend clipping until the end pattern)"),
)


class SettingsUI:
    def __init__(self, parent, on_theme_change=None):
        self.parent = parent
        self._on_theme_change = on_theme_change

        self.config = app_config.load_config()

        self.theme_var = tk.StringVar()
        self.preset_var = tk.StringVar()
        self.obs_host_var = tk.StringVar()
        self.obs_port_var = tk.StringVar()
        self.obs_password_var = tk.StringVar()
        self.recording_folder_var = tk.StringVar()
        self.output_folder_var = tk.StringVar()
        self.full_transcript_srt_folder_var = tk.StringVar()
        self.audio_source_type_var = tk.StringVar()
        self.audio_device_name_var = tk.StringVar()
        self.audio_device_fallback_var = tk.StringVar()
        self.remote_api_enabled_var = tk.BooleanVar()
        self.remote_api_port_var = tk.StringVar()
        self.whisper_model_var = tk.StringVar()
        self.whisper_device_var = tk.StringVar()
        self.whisper_compute_type_var = tk.StringVar()
        self.verify_model_var = tk.StringVar()
        self.verify_device_var = tk.StringVar()
        self.verify_compute_type_var = tk.StringVar()
        self.possible_threshold_var = tk.StringVar()
        self.save_threshold_var = tk.StringVar()
        self.verified_similarity_var = tk.StringVar()
        self.verified_confidence_var = tk.StringVar()
        self.audio_excitement_enabled_var = tk.BooleanVar()
        self.audio_excitement_moderate_ratio_var = tk.StringVar()
        self.audio_excitement_strong_ratio_var = tk.StringVar()
        self.audio_excitement_moderate_bonus_var = tk.StringVar()
        self.audio_excitement_strong_bonus_var = tk.StringVar()
        self.pause_end_pattern_var = tk.StringVar()

        self.render_style_var = tk.StringVar()
        self.apply_vertical_layout_var = tk.BooleanVar()
        self.render_encoder_var = tk.StringVar()
        self.burn_in_captions_var = tk.BooleanVar()
        self.caption_style_var = tk.StringVar()
        self.caption_font_name_var = tk.StringVar()
        self.caption_font_size_var = tk.StringVar()
        self.caption_bold_var = tk.BooleanVar()
        self.caption_text_color_var = tk.StringVar()
        self.caption_outline_color_var = tk.StringVar()
        self.caption_outline_width_var = tk.StringVar()
        self.caption_shadow_var = tk.StringVar()
        self.caption_background_enabled_var = tk.BooleanVar()
        self.caption_background_color_var = tk.StringVar()
        self.caption_background_opacity_var = tk.StringVar()
        self.caption_position_var = tk.StringVar()
        self.caption_margin_h_var = tk.StringVar()
        self.caption_margin_v_var = tk.StringVar()
        self.caption_max_words_var = tk.StringVar()

        self.phrase_list_widgets = {}
        self.whisper_prompt_widget = None

        self._preset_key_by_label = {}

        self._build_ui()
        self._load_config_into_fields()

    # -----------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------

    def _build_ui(self):
        outer = ttk.Frame(self.parent, padding=16)
        outer.pack(fill="both", expand=True)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        preset_tab = ttk.Frame(notebook, padding=12)
        connection_tab = ttk.Frame(notebook, padding=12)
        model_tab = ttk.Frame(notebook, padding=12)
        caption_tab = ttk.Frame(notebook, padding=12)
        scene_rules_tab = ttk.Frame(notebook, padding=12)
        about_tab = ttk.Frame(notebook, padding=12)

        notebook.add(preset_tab, text="Preset & Phrases")
        notebook.add(connection_tab, text="OBS & Folders")
        notebook.add(model_tab, text="Whisper & Thresholds")
        notebook.add(caption_tab, text="Video Style")
        notebook.add(scene_rules_tab, text="Scene Rules")
        notebook.add(about_tab, text="About")

        self._build_preset_tab(preset_tab)
        self._build_connection_tab(connection_tab)
        self._build_model_tab(model_tab)
        self._build_caption_style_tab(caption_tab)
        self._build_scene_rules_tab(scene_rules_tab)
        self._build_about_tab(about_tab)

        button_row = ttk.Frame(outer, padding=(0, 12, 0, 0))
        button_row.pack(fill="x")

        ttk.Button(
            button_row,
            text="Save",
            command=self._save,
        ).pack(side="right")

    def _make_scrollable(self, parent):
        """Wraps a tab's content in a vertically scrollable area and
        returns the inner frame to build that tab's widgets into instead
        of `parent` directly - some tabs (Video Style especially, with
        layout/encoder/caption-style controls and a preview all stacked
        in one place) can end up taller than the window, particularly
        when Settings is embedded in the smaller main app window rather
        than run standalone. Every existing `ttk.Widget(parent, ...)`
        call in a tab-builder keeps working unchanged - only the value
        `parent` is bound to changes."""
        canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Only a right-side gap before the scrollbar - the tab frame
        # itself already carries padding=12 on every side (set where
        # each tab is created), which still applies around the canvas.
        inner = ttk.Frame(canvas, padding=(0, 0, 12, 0))
        inner_window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _update_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _resize_inner_to_canvas(event):
            canvas.itemconfigure(inner_window, width=event.width)

        inner.bind("<Configure>", _update_scroll_region)
        canvas.bind("<Configure>", _resize_inner_to_canvas)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        return inner

    def _build_preset_tab(self, parent):
        parent = self._make_scrollable(parent)

        top_row = ttk.Frame(parent)
        top_row.pack(fill="x", pady=(0, 6))

        ttk.Label(top_row, text="Preset:").pack(side="left")

        self.preset_combo = ttk.Combobox(
            top_row,
            textvariable=self.preset_var,
            state="readonly",
            width=24,
        )
        self.preset_combo.pack(side="left", padx=(8, 0))
        self.preset_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_preset_changed())

        profile_button_row = ttk.Frame(parent)
        profile_button_row.pack(fill="x", pady=(0, 10))

        ttk.Button(profile_button_row, text="New...", command=self._new_profile).pack(side="left")
        ttk.Button(profile_button_row, text="Duplicate...", command=self._duplicate_profile).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(profile_button_row, text="Delete", command=self._delete_profile).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(profile_button_row, text="Export...", command=self._export_profile).pack(
            side="left", padx=(16, 0)
        )
        ttk.Button(profile_button_row, text="Import...", command=self._import_profile).pack(
            side="left", padx=(6, 0)
        )

        hint = ttk.Label(
            parent,
            text=(
                "Built-in presets are read-only. Duplicate one (or use New) to make "
                "your own named profile - it's saved and reused next time."
            ),
            wraplength=680,
            foreground="#888888",
        )
        hint.pack(fill="x", pady=(0, 10))

        self._refresh_preset_dropdown_values()

        for field, label in PHRASE_LIST_FIELDS:
            ttk.Label(parent, text=label).pack(fill="x")
            text_widget = tk.Text(parent, height=3, wrap="word")
            text_widget.pack(fill="x", pady=(0, 8))
            self.phrase_list_widgets[field] = text_widget

        ttk.Label(parent, text="Pause end pattern (regex)").pack(fill="x")
        ttk.Entry(parent, textvariable=self.pause_end_pattern_var).pack(fill="x", pady=(0, 8))

        ttk.Label(parent, text="Whisper vocabulary hint (whisper_initial_prompt)").pack(fill="x")
        prompt_widget = tk.Text(parent, height=4, wrap="word")
        prompt_widget.pack(fill="both", expand=True)
        self.whisper_prompt_widget = prompt_widget

    def _build_connection_tab(self, parent):
        self._add_labeled_entry(parent, "OBS host", self.obs_host_var)
        self._add_labeled_entry(parent, "OBS port", self.obs_port_var)

        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="OBS password", width=36, anchor="w").pack(side="left")
        ttk.Entry(row, textvariable=self.obs_password_var, show="*").pack(side="left", fill="x", expand=True)

        ttk.Label(
            parent,
            text=(
                "Leave blank to keep the current password. It's stored in a "
                "local .env file, never in highlight_config.json."
            ),
            wraplength=680,
            foreground="#888888",
        ).pack(fill="x", pady=(0, 12))

        self._add_labeled_folder(parent, "Recording folder", self.recording_folder_var)

        self._add_labeled_folder(parent, "Output folder", self.output_folder_var)
        ttk.Label(
            parent,
            text="Leave blank to save under the recording folder instead.",
            wraplength=680,
            foreground="#888888",
        ).pack(fill="x", pady=(0, 8))

        self._add_labeled_folder(parent, "Full-session SRT folder", self.full_transcript_srt_folder_var)
        ttk.Label(
            parent,
            text="Optional - cross-checks a clip's trim against the full recording's own transcript.",
            wraplength=680,
            foreground="#888888",
        ).pack(fill="x", pady=(0, 8))

        self._add_audio_device_row(parent)
        self._add_labeled_entry(parent, "Audio device fallback index", self.audio_device_fallback_var)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=10)

        self._add_labeled_checkbox(
            parent, "Remote API enabled (Stream Deck / Companion / etc.)", self.remote_api_enabled_var
        )
        self._add_labeled_spinbox(parent, "Remote API port", self.remote_api_port_var, 1024, 65535, 1)
        ttk.Label(
            parent,
            text=(
                "Only reachable from this machine (127.0.0.1), never the network. "
                "Only active while Highlight Capture is running - see the README for the endpoints."
            ),
            wraplength=680,
            foreground="#888888",
        ).pack(fill="x", pady=(0, 8))

        ttk.Button(
            parent,
            text="Save",
            command=self._save,
        ).pack(anchor="e", pady=(16, 0))

    def _build_model_tab(self, parent):
        self._add_labeled_combo(parent, "Whisper model (live capture)", self.whisper_model_var, WHISPER_MODEL_OPTIONS)
        self._add_labeled_combo(parent, "Whisper device", self.whisper_device_var, DEVICE_OPTIONS)
        self._add_labeled_combo(parent, "Whisper compute type", self.whisper_compute_type_var, COMPUTE_TYPE_OPTIONS)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=10)

        self._add_labeled_combo(parent, "Verify model (second pass)", self.verify_model_var, WHISPER_MODEL_OPTIONS)
        self._add_labeled_combo(parent, "Verify device", self.verify_device_var, DEVICE_OPTIONS)
        self._add_labeled_combo(parent, "Verify compute type", self.verify_compute_type_var, COMPUTE_TYPE_OPTIONS)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=10)

        self._add_labeled_spinbox(parent, "Possible-clip threshold (0-100)", self.possible_threshold_var, 0, 100, 1)
        self._add_labeled_spinbox(parent, "Save-clip threshold (0-100)", self.save_threshold_var, 0, 100, 1)
        self._add_labeled_spinbox(parent, "Verified similarity (0-100)", self.verified_similarity_var, 0, 100, 1)
        self._add_labeled_spinbox(
            parent,
            "Verified confidence (0.0-1.0)",
            self.verified_confidence_var,
            0.0,
            1.0,
            0.05,
            fmt="%.2f",
        )

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=10)

        self._add_labeled_checkbox(parent, "Audio excitement enabled", self.audio_excitement_enabled_var, label_width=36)
        self._add_labeled_spinbox(
            parent, "Moderate spike ratio (x baseline)", self.audio_excitement_moderate_ratio_var, 1.0, 5.0, 0.1, fmt="%.1f"
        )
        self._add_labeled_spinbox(
            parent, "Strong spike ratio (x baseline)", self.audio_excitement_strong_ratio_var, 1.0, 5.0, 0.1, fmt="%.1f"
        )
        self._add_labeled_spinbox(parent, "Moderate spike bonus", self.audio_excitement_moderate_bonus_var, 0, 50, 1)
        self._add_labeled_spinbox(parent, "Strong spike bonus", self.audio_excitement_strong_bonus_var, 0, 50, 1)

        ttk.Frame(parent).pack(fill="both", expand=True)

        ttk.Button(
            parent,
            text="What do these settings mean?",
            command=self._show_model_info,
        ).pack(anchor="w", pady=(10, 0))

    def _show_model_info(self):
        messagebox.showinfo(
            "Whisper & Thresholds",
            "Whisper model - bigger models (medium/large) are more "
            "accurate but slower and use more GPU memory. The live "
            "capture model should stay small/fast since it runs "
            "continuously; the verify model can be bigger since it "
            "only re-checks clips after they're already saved.\n\n"
            "Device - \"cuda\" uses your NVIDIA GPU (fast, needs a "
            "compatible GPU and drivers). \"cpu\" works everywhere but "
            "is much slower. \"auto\" lets the library decide.\n\n"
            "Compute type - trades accuracy for speed/memory. float32 "
            "is most accurate and slowest; float16/int8_float16 are "
            "good GPU defaults; int8 is the usual CPU choice.\n\n"
            "Possible/Save-clip threshold - how selective the live "
            "scorer is (0-100). Lower catches more moments, including "
            "weaker ones; higher only saves the strongest.\n\n"
            "Verified similarity/confidence - how closely a clip's "
            "second, more accurate transcription has to match the "
            "original before it's marked Verified instead of sent to "
            "Review.\n\n"
            "Audio excitement - adds bonus points when a moment is "
            "noticeably louder than the recent rolling average (a "
            "loudness spike), catching cheering/shouting/reactions even "
            "when nothing said matches a specific phrase. The ratio is "
            "how many times louder than baseline counts as a spike; "
            "the bonus is how many points that's worth.",
        )

    def _build_caption_style_tab(self, parent):
        parent = self._make_scrollable(parent)

        render_row = ttk.Frame(parent)
        render_row.pack(fill="x", pady=(0, 4))

        ttk.Label(render_row, text="Video layout:").pack(side="left")

        render_style_labels = [style["label"] for style in render_styles.RENDER_STYLES.values()]
        self.render_style_combo = ttk.Combobox(
            render_row,
            textvariable=self.render_style_var,
            values=render_style_labels,
            state="readonly",
            width=22,
        )
        self.render_style_combo.pack(side="left", padx=(8, 0))
        self.render_style_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_render_style_hint())

        self.render_style_hint_label = ttk.Label(
            parent, text="", wraplength=680, foreground="#888888"
        )
        self.render_style_hint_label.pack(fill="x", pady=(2, 6))

        ttk.Label(
            parent,
            text=(
                "Face Cam + Gameplay and Top/Bottom layouts aren't available yet - they'd need OBS "
                "recording separate camera/gameplay tracks, not just a render option."
            ),
            wraplength=680,
            foreground="#888888",
        ).pack(fill="x", pady=(0, 6))

        ttk.Checkbutton(
            parent,
            text="Apply this layout when rendering",
            variable=self.apply_vertical_layout_var,
        ).pack(anchor="w", pady=(0, 2))

        ttk.Label(
            parent,
            text=(
                "Turn off to render the verified clip trimmed to its start/end point at its "
                "original resolution and aspect ratio, untouched otherwise - for finishing it "
                "yourself in another editor instead of using this tool's layout."
            ),
            wraplength=680,
            foreground="#888888",
        ).pack(fill="x", pady=(0, 10))

        encoder_row = ttk.Frame(parent)
        encoder_row.pack(fill="x", pady=(0, 4))

        ttk.Label(encoder_row, text="Render encoder:").pack(side="left")

        self.render_encoder_combo = ttk.Combobox(
            encoder_row,
            textvariable=self.render_encoder_var,
            values=list(RENDER_ENCODER_LABELS.values()),
            state="readonly",
            width=32,
        )
        self.render_encoder_combo.pack(side="left", padx=(8, 0))

        ttk.Label(
            parent,
            text=(
                "CPU / Software works on any machine but is much slower than NVENC. NVENC also "
                "auto-detects at render time and falls back to CPU on its own if it isn't actually "
                "usable - set this explicitly only to skip that check or force software encoding."
            ),
            wraplength=680,
            foreground="#888888",
        ).pack(fill="x", pady=(2, 10))

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=(0, 10))

        ttk.Checkbutton(
            parent,
            text="Burn in captions when rendering",
            variable=self.burn_in_captions_var,
        ).pack(anchor="w", pady=(0, 2))

        ttk.Label(
            parent,
            text=(
                "Turn off to render without the captions below baked into the video - the .srt "
                "caption file is still saved in Verified/, so you can add your own captions in "
                "another editor if you'd rather not use this tool's style."
            ),
            wraplength=680,
            foreground="#888888",
        ).pack(fill="x", pady=(0, 10))

        top_row = ttk.Frame(parent)
        top_row.pack(fill="x", pady=(0, 6))

        ttk.Label(top_row, text="Style:").pack(side="left")

        style_labels = [style["label"] for style in caption_styles.CAPTION_STYLES.values()] + ["Custom"]
        self.caption_style_combo = ttk.Combobox(
            top_row,
            textvariable=self.caption_style_var,
            values=style_labels,
            state="readonly",
            width=20,
        )
        self.caption_style_combo.pack(side="left", padx=(8, 0))
        self.caption_style_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_caption_style_changed())

        ttk.Label(
            parent,
            text="Built-in styles are read-only. Choose Custom to tune your own.",
            wraplength=680,
            foreground="#888888",
        ).pack(fill="x", pady=(0, 10))

        columns = ttk.Frame(parent)
        columns.pack(fill="x")

        left = ttk.Frame(columns)
        left.pack(side="left", fill="both", expand=True)

        right = ttk.Frame(columns)
        right.pack(side="left", fill="both", expand=True, padx=(20, 0))

        self.caption_style_widgets = [
            self._add_labeled_entry(left, "Font name", self.caption_font_name_var, label_width=18),
            self._add_labeled_spinbox(
                left, "Font size", self.caption_font_size_var, 10, 72, 1, label_width=18
            ),
            self._add_labeled_checkbox(left, "Bold", self.caption_bold_var, label_width=18),
            self._add_labeled_combo(
                left, "Position", self.caption_position_var, ("bottom", "middle", "top"), label_width=18
            ),
            self._add_labeled_spinbox(
                left, "Max words/line", self.caption_max_words_var, 1, 15, 1, label_width=18
            ),
            self._add_labeled_spinbox(
                left, "Horizontal margin", self.caption_margin_h_var, 0, 300, 10, label_width=18
            ),
            self._add_labeled_spinbox(
                left, "Vertical margin", self.caption_margin_v_var, 0, 600, 10, label_width=18
            ),
        ]

        text_color_entry, text_color_button = self._add_labeled_color(
            right, "Text color", self.caption_text_color_var, label_width=18
        )
        outline_color_entry, outline_color_button = self._add_labeled_color(
            right, "Outline color", self.caption_outline_color_var, label_width=18
        )
        background_color_entry, background_color_button = self._add_labeled_color(
            right, "Background color", self.caption_background_color_var, label_width=18
        )

        self.caption_style_widgets.extend([
            text_color_entry,
            text_color_button,
            outline_color_entry,
            outline_color_button,
            self._add_labeled_spinbox(
                right, "Outline width", self.caption_outline_width_var, 0, 10, 1, label_width=18
            ),
            self._add_labeled_spinbox(right, "Shadow", self.caption_shadow_var, 0, 5, 1, label_width=18),
            self._add_labeled_checkbox(
                right, "Background box", self.caption_background_enabled_var, label_width=18
            ),
            background_color_entry,
            background_color_button,
            self._add_labeled_spinbox(
                right, "Background opacity %", self.caption_background_opacity_var, 0, 100, 5, label_width=18
            ),
        ])

        ttk.Label(
            parent, text="Preview (approximate style only - not an actual video render)"
        ).pack(anchor="w", pady=(14, 4))

        self.caption_preview_canvas = tk.Canvas(
            parent, height=140, background="#202020", highlightthickness=1, highlightbackground="#555555"
        )
        self.caption_preview_canvas.pack(fill="x")

        button_row = ttk.Frame(parent)
        button_row.pack(fill="x", pady=(12, 0))

        ttk.Button(button_row, text="Refresh Preview", command=self._update_caption_preview).pack(side="left")
        ttk.Button(button_row, text="Save", command=self._save).pack(side="right")

    def _current_theme_key(self):
        return ui_theme.THEME_KEY_BY_LABEL.get(self.theme_var.get(), ui_theme.DEFAULT_THEME)

    def _on_theme_selected(self):
        # Applies instantly (sv_ttk is built for live switching) and
        # persists right away too - unlike the rest of this form, a
        # theme choice isn't something you'd want to preview then
        # discard by navigating away without pressing Save.
        theme = self._current_theme_key()

        ui_theme.apply_theme(theme)
        self._update_about_logo(theme)

        config = app_config.load_config()
        config["theme"] = theme
        app_config.save_config(config)

        if self._on_theme_change:
            self._on_theme_change(theme)

    def _update_about_logo(self, theme):
        """The logo is drawn as light lines on a transparent background -
        readable on About's dark-mode background, close to invisible on
        light. A pre-made dark-line variant (same artwork, colors
        inverted, transparency untouched) swaps in for light mode
        instead of inverting pixels at runtime, so this needs no image
        library as a dependency - just two PNGs to choose between."""
        filename = (
            "vivce_media_solutions_ui_light.png"
            if theme == "light"
            else "vivce_media_solutions_ui.png"
        )

        logo_path = app_config.resource_path("assets", filename)

        try:
            self._about_logo_image = tk.PhotoImage(file=str(logo_path))
            self._about_logo_label.configure(image=self._about_logo_image)
        except Exception:
            pass

    def _current_render_style_key(self):
        label = self.render_style_var.get()
        for key, style in render_styles.RENDER_STYLES.items():
            if style["label"] == label:
                return key
        return render_styles.DEFAULT_STYLE

    def _update_render_style_hint(self):
        style = render_styles.get_style(self._current_render_style_key())
        self.render_style_hint_label.configure(text=style["description"])

    def _on_caption_style_changed(self):
        self._display_caption_style(self._current_caption_style_key())
        self._update_caption_preview()

    def _current_caption_style_key(self):
        label = self.caption_style_var.get()
        for key, style in caption_styles.CAPTION_STYLES.items():
            if style["label"] == label:
                return key
        return "custom"

    def _display_caption_style(self, style_key):
        is_custom = style_key == "custom"
        style = caption_styles.get_style(style_key, self.config.get("custom_caption_style"))

        self.caption_font_name_var.set(style.get("font_name", "Arial"))
        self.caption_font_size_var.set(str(style.get("font_size", 24)))
        self.caption_bold_var.set(bool(style.get("bold", True)))
        self.caption_text_color_var.set(style.get("text_color", "#FFFFFF"))
        self.caption_outline_color_var.set(style.get("outline_color", "#000000"))
        self.caption_outline_width_var.set(str(style.get("outline_width", 3)))
        self.caption_shadow_var.set(str(style.get("shadow", 1)))
        self.caption_background_enabled_var.set(bool(style.get("background_enabled", False)))
        self.caption_background_color_var.set(style.get("background_color", "#000000"))
        self.caption_background_opacity_var.set(str(style.get("background_opacity", 50)))
        self.caption_position_var.set(style.get("position", "bottom"))
        self.caption_margin_h_var.set(str(style.get("margin_h", 90)))
        self.caption_margin_v_var.set(str(style.get("margin_v", 300)))
        self.caption_max_words_var.set(str(style.get("max_words_per_line", 7)))

        state = "normal" if is_custom else "disabled"
        for widget in self.caption_style_widgets:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

    def _collect_caption_style_fields(self):
        return {
            "font_name": self.caption_font_name_var.get().strip() or "Arial",
            "font_size": int(self.caption_font_size_var.get()),
            "bold": self.caption_bold_var.get(),
            "text_color": self.caption_text_color_var.get().strip() or "#FFFFFF",
            "outline_color": self.caption_outline_color_var.get().strip() or "#000000",
            "outline_width": int(self.caption_outline_width_var.get()),
            "shadow": int(self.caption_shadow_var.get()),
            "background_enabled": self.caption_background_enabled_var.get(),
            "background_color": self.caption_background_color_var.get().strip() or "#000000",
            "background_opacity": int(self.caption_background_opacity_var.get()),
            "position": self.caption_position_var.get().strip() or "bottom",
            "margin_h": int(self.caption_margin_h_var.get()),
            "margin_v": int(self.caption_margin_v_var.get()),
            "max_words_per_line": int(self.caption_max_words_var.get()),
        }

    def _update_caption_preview(self):
        canvas = self.caption_preview_canvas
        canvas.delete("all")
        canvas.update_idletasks()

        width = canvas.winfo_width() or 600
        height = 140

        try:
            fields = self._collect_caption_style_fields()
        except ValueError:
            return

        y = {"bottom": height - 25, "middle": height // 2, "top": 25}.get(fields["position"], height - 25)

        font_spec = (fields["font_name"], max(8, fields["font_size"] // 2), "bold" if fields["bold"] else "normal")

        if fields["background_enabled"]:
            canvas.create_rectangle(
                0, y - 20, width, y + 20, fill=fields["background_color"], outline="", stipple="gray50"
            )

        canvas.create_text(
            width / 2,
            y,
            text="Sample Caption Text",
            fill=fields["text_color"],
            font=font_spec,
        )

    def _build_scene_rules_tab(self, parent):
        parent = self._make_scrollable(parent)

        ttk.Label(
            parent,
            text=(
                "Automatically pause clipping, ignore a scene entirely, or switch preset "
                "based on OBS's current scene name (checked while Highlight Capture is running)."
            ),
            wraplength=680,
        ).pack(anchor="w", pady=(0, 10))

        self.scene_rules_container = ttk.Frame(parent)
        self.scene_rules_container.pack(fill="both", expand=True)

        self.scene_rule_rows = []

        button_row = ttk.Frame(parent)
        button_row.pack(fill="x", pady=(10, 0))

        ttk.Button(button_row, text="+ Add Rule", command=self._add_scene_rule_row).pack(side="left")
        ttk.Button(button_row, text="Save", command=self._save).pack(side="right")

    def _preset_labels_for_rules(self):
        return list(self._preset_key_by_label.keys())

    def _label_for_preset_key(self, key):
        for label, candidate_key in self._preset_key_by_label.items():
            if candidate_key == key:
                return label
        return ""

    def _add_scene_rule_row(self, match="", action_key="pause", preset_key=""):
        row = ttk.Frame(self.scene_rules_container)
        row.pack(fill="x", pady=3)

        ttk.Label(row, text="Scene contains:").pack(side="left")
        match_var = tk.StringVar(value=match)
        ttk.Entry(row, textvariable=match_var, width=18).pack(side="left", padx=(4, 12))

        ttk.Label(row, text="Action:").pack(side="left")
        action_var = tk.StringVar(value=SCENE_ACTION_LABELS.get(action_key, SCENE_ACTION_LABELS["pause"]))
        ttk.Combobox(
            row,
            textvariable=action_var,
            values=list(SCENE_ACTION_LABELS.values()),
            state="readonly",
            width=16,
        ).pack(side="left", padx=(4, 12))

        ttk.Label(row, text="Preset:").pack(side="left")
        preset_var = tk.StringVar(value=self._label_for_preset_key(preset_key))
        preset_combo = ttk.Combobox(
            row,
            textvariable=preset_var,
            values=self._preset_labels_for_rules(),
            state="readonly",
            width=18,
        )
        preset_combo.pack(side="left", padx=(4, 12))

        row_data = {
            "frame": row,
            "match_var": match_var,
            "action_var": action_var,
            "preset_var": preset_var,
            "preset_combo": preset_combo,
        }

        ttk.Button(
            row, text="Remove", command=lambda: self._remove_scene_rule_row(row_data)
        ).pack(side="left")

        self.scene_rule_rows.append(row_data)
        return row_data

    def _remove_scene_rule_row(self, row_data):
        row_data["frame"].destroy()
        self.scene_rule_rows.remove(row_data)

    def _load_scene_rules(self, rules):
        for row_data in list(self.scene_rule_rows):
            self._remove_scene_rule_row(row_data)

        for rule in rules:
            self._add_scene_rule_row(
                match=rule.get("match", ""),
                action_key=rule.get("action", "pause"),
                preset_key=rule.get("preset", ""),
            )

    def _collect_scene_rules(self):
        rules = []

        for row_data in self.scene_rule_rows:
            match_text = row_data["match_var"].get().strip()
            if not match_text:
                continue

            action_key = SCENE_ACTION_KEY_BY_LABEL.get(row_data["action_var"].get(), "pause")
            rule = {"match": match_text, "action": action_key}

            if action_key == "preset":
                preset_key = self._preset_key_by_label.get(row_data["preset_var"].get())
                if preset_key:
                    rule["preset"] = preset_key

            rules.append(rule)

        return rules

    def _build_about_tab(self, parent):
        appearance_row = ttk.Frame(parent)
        appearance_row.pack(fill="x", padx=20, pady=(16, 0))

        ttk.Label(appearance_row, text="Theme:").pack(side="left")

        self.theme_combo = ttk.Combobox(
            appearance_row,
            textvariable=self.theme_var,
            values=list(ui_theme.THEME_LABELS.values()),
            state="readonly",
            width=10,
        )
        self.theme_combo.pack(side="left", padx=(8, 0))
        self.theme_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_theme_selected())

        content = ttk.Frame(parent)
        content.place(relx=0.5, rely=0.5, anchor="center")

        # _build_ui() calls this before _load_config_into_fields() sets
        # self.theme_var, so the initial pick reads self.config (already
        # loaded in __init__) directly rather than the not-yet-populated
        # StringVar - _on_theme_selected() keeps this in sync afterward.
        self._about_logo_label = ttk.Label(content)
        self._about_logo_label.pack(pady=(0, 12))
        self._update_about_logo(ui_theme.get_theme(self.config))

        ttk.Label(
            content,
            text="Vice Media Solutions",
            font=("TkDefaultFont", 13, "bold"),
        ).pack()

        ttk.Label(
            content,
            text="OBS AI Highlights",
            foreground="#888888",
        ).pack()

    def _add_labeled_combo(self, parent, label, var, values, label_width=36):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=label_width, anchor="w").pack(side="left")
        widget = ttk.Combobox(row, textvariable=var, values=values)
        widget.pack(side="left", fill="x", expand=True)
        return widget

    def _add_labeled_spinbox(self, parent, label, var, from_, to, increment, fmt=None, label_width=36):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=label_width, anchor="w").pack(side="left")

        kwargs = {}
        if fmt:
            kwargs["format"] = fmt

        widget = ttk.Spinbox(
            row,
            from_=from_,
            to=to,
            increment=increment,
            textvariable=var,
            **kwargs,
        )
        widget.pack(side="left", fill="x", expand=True)
        return widget

    def _add_labeled_checkbox(self, parent, label, var, label_width=20):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=label_width, anchor="w").pack(side="left")
        widget = ttk.Checkbutton(row, variable=var)
        widget.pack(side="left")
        return widget

    def _add_labeled_color(self, parent, label, var, label_width=20):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=label_width, anchor="w").pack(side="left")
        entry = ttk.Entry(row, textvariable=var, width=10)
        entry.pack(side="left")
        button = ttk.Button(row, text="Pick...", command=lambda v=var: self._pick_color(v))
        button.pack(side="left", padx=(4, 0))
        return entry, button

    def _pick_color(self, var):
        current = var.get().strip() or "#FFFFFF"
        try:
            _rgb, hex_value = colorchooser.askcolor(color=current, parent=self.parent)
        except tk.TclError:
            _rgb, hex_value = colorchooser.askcolor(parent=self.parent)

        if hex_value:
            var.set(hex_value.upper())

    def _add_audio_device_row(self, parent):
        type_row = ttk.Frame(parent)
        type_row.pack(fill="x", pady=4)
        ttk.Label(type_row, text="Audio source", width=36, anchor="w").pack(side="left")

        self.audio_source_type_combo = ttk.Combobox(
            type_row,
            textvariable=self.audio_source_type_var,
            state="readonly",
            values=list(AUDIO_SOURCE_TYPE_LABELS.values()),
        )
        self.audio_source_type_combo.pack(side="left", fill="x", expand=True)
        self.audio_source_type_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._refresh_audio_devices()
        )

        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        self.audio_device_row_label = ttk.Label(row, text="Audio device", width=36, anchor="w")
        self.audio_device_row_label.pack(side="left")

        self.audio_device_combo = ttk.Combobox(row, textvariable=self.audio_device_name_var)
        self.audio_device_combo.pack(side="left", fill="x", expand=True)

        ttk.Button(
            row,
            text="Refresh",
            command=self._refresh_audio_devices,
        ).pack(side="left", padx=(6, 0))

        self._refresh_audio_devices()

    def _refresh_audio_devices(self):
        is_microphone_mode = (
            AUDIO_SOURCE_TYPE_KEY_BY_LABEL.get(self.audio_source_type_var.get())
            == "microphone"
        )

        if is_microphone_mode:
            devices = _enumerate_input_devices()
            self.audio_device_row_label.configure(text="Audio device (microphone)")
        else:
            devices = _enumerate_loopback_devices()
            self.audio_device_row_label.configure(text="Audio device (loopback)")

        current = self.audio_device_name_var.get()

        self.audio_device_combo.configure(values=devices)

        # Enumerating can fail to find hardware that's actually
        # configured (unplugged, driver hiccup, etc.) - never wipe out
        # an existing saved value just because a refresh came back
        # empty or didn't include it.
        self.audio_device_name_var.set(current)

    def _add_labeled_entry(self, parent, label, var, label_width=36):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=label_width, anchor="w").pack(side="left")
        widget = ttk.Entry(row, textvariable=var)
        widget.pack(side="left", fill="x", expand=True)
        return widget

    def _add_labeled_folder(self, parent, label, var):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=36, anchor="w").pack(side="left")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        ttk.Button(
            row,
            text="Browse…",
            command=lambda v=var: self._browse_folder(v),
        ).pack(side="left", padx=(6, 0))

    def _browse_folder(self, var):
        # A blank field used to fall back to ".", which for a Start-Menu-
        # launched app with no explicit shortcut working directory is the
        # app's own install folder - an easy, genuinely-hit mistake to
        # pick as your "output folder" and one that later crashes with a
        # PermissionError, since Program Files isn't writable. Default to
        # the user's home folder instead - always exists, never that.
        chosen = filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
        if chosen:
            var.set(chosen)

    # -----------------------------------------------------------
    # Config <-> fields
    # -----------------------------------------------------------

    def _load_config_into_fields(self):
        config = self.config

        self.theme_var.set(ui_theme.THEME_LABELS[ui_theme.get_theme(config)])

        active_key = config.get("preset", presets.DEFAULT_PRESET)
        active_preset = presets.get_preset(active_key, config.get("custom_profiles", {}))
        self.preset_var.set(active_preset["label"])

        self.obs_host_var.set(str(config.get("obs_host", "")))
        self.obs_port_var.set(str(config.get("obs_port", "")))
        self.recording_folder_var.set(str(config.get("recording_folder", "")))
        self.output_folder_var.set(str(config.get("output_folder", "")))
        self.full_transcript_srt_folder_var.set(str(config.get("full_transcript_srt_folder", "")))

        audio_source_type = config.get(
            "audio_source_type", app_config.DEFAULTS["audio_source_type"]
        )
        self.audio_source_type_var.set(
            AUDIO_SOURCE_TYPE_LABELS.get(audio_source_type, AUDIO_SOURCE_TYPE_LABELS["loopback"])
        )
        self.audio_device_name_var.set(str(config.get("audio_device_name", "")))
        self._refresh_audio_devices()
        self.audio_device_fallback_var.set(str(config.get("audio_device_fallback_index", 0)))
        self.remote_api_enabled_var.set(bool(config.get("remote_api_enabled", True)))
        self.remote_api_port_var.set(str(config.get("remote_api_port", 8756)))
        self.whisper_model_var.set(str(config.get("whisper_model", "")))
        self.whisper_device_var.set(str(config.get("whisper_device", "")))
        self.whisper_compute_type_var.set(str(config.get("whisper_compute_type", "")))
        self.verify_model_var.set(str(config.get("verify_model", "")))
        self.verify_device_var.set(str(config.get("verify_device", "")))
        self.verify_compute_type_var.set(str(config.get("verify_compute_type", "")))
        self.possible_threshold_var.set(str(config.get("possible_threshold", "")))
        self.save_threshold_var.set(str(config.get("save_threshold", "")))
        self.verified_similarity_var.set(str(config.get("verified_similarity", "")))
        self.verified_confidence_var.set(str(config.get("verified_confidence", "")))
        self.audio_excitement_enabled_var.set(bool(config.get("audio_excitement_enabled", True)))
        self.audio_excitement_moderate_ratio_var.set(str(config.get("audio_excitement_moderate_ratio", 1.6)))
        self.audio_excitement_strong_ratio_var.set(str(config.get("audio_excitement_strong_ratio", 2.5)))
        self.audio_excitement_moderate_bonus_var.set(str(config.get("audio_excitement_moderate_bonus", 10)))
        self.audio_excitement_strong_bonus_var.set(str(config.get("audio_excitement_strong_bonus", 18)))

        self._display_preset_phrases(active_key)

        active_render_key = config.get("render_style", render_styles.DEFAULT_STYLE)
        self.render_style_var.set(render_styles.get_style(active_render_key)["label"])
        self._update_render_style_hint()
        self.apply_vertical_layout_var.set(bool(config.get("apply_vertical_layout", True)))

        active_encoder_key = config.get("render_encoder", "nvenc")
        self.render_encoder_var.set(
            RENDER_ENCODER_LABELS.get(active_encoder_key, RENDER_ENCODER_LABELS["nvenc"])
        )

        self.burn_in_captions_var.set(bool(config.get("burn_in_captions", True)))

        active_caption_key = config.get("caption_style", caption_styles.DEFAULT_STYLE)
        active_caption_style = caption_styles.get_style(active_caption_key, config.get("custom_caption_style"))
        self.caption_style_var.set(active_caption_style["label"])
        self._display_caption_style(active_caption_key)
        self._update_caption_preview()

        self._load_scene_rules(config.get("scene_rules", []))

    def _current_preset_key(self):
        return self._preset_key_by_label.get(self.preset_var.get(), presets.DEFAULT_PRESET)

    def _on_preset_changed(self):
        self._display_preset_phrases(self._current_preset_key())

    def _display_preset_phrases(self, preset_key):
        is_custom = presets.is_custom_key(preset_key)
        preset = presets.get_preset(preset_key, self.config.get("custom_profiles", {}))

        for field, _label in PHRASE_LIST_FIELDS:
            widget = self.phrase_list_widgets[field]
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", "\n".join(preset.get(field, [])))
            widget.configure(state="normal" if is_custom else "disabled")

        self.pause_end_pattern_var.set(preset.get("pause_end_pattern", ""))

        self.whisper_prompt_widget.configure(state="normal")
        self.whisper_prompt_widget.delete("1.0", "end")
        self.whisper_prompt_widget.insert("1.0", preset.get("whisper_initial_prompt", "").strip())
        self.whisper_prompt_widget.configure(state="normal" if is_custom else "disabled")

    def _collect_profile_fields(self):
        fields = {}
        for field, _label in PHRASE_LIST_FIELDS:
            raw = self.phrase_list_widgets[field].get("1.0", "end")
            fields[field] = [line.strip() for line in raw.splitlines() if line.strip()]

        fields["pause_end_pattern"] = self.pause_end_pattern_var.get().strip()
        fields["whisper_initial_prompt"] = self.whisper_prompt_widget.get("1.0", "end").strip()
        return fields

    # -----------------------------------------------------------
    # Named custom profiles: New / Duplicate / Delete / Export / Import
    # -----------------------------------------------------------

    def _refresh_preset_dropdown_values(self):
        self._preset_key_by_label = {}

        for key, preset in presets.PRESETS.items():
            if key == "custom":
                continue  # the blank template - not directly selectable, only named profiles are
            self._preset_key_by_label[preset["label"]] = key

        for name in sorted(self.config.get("custom_profiles", {}).keys()):
            self._preset_key_by_label[name] = presets.make_custom_key(name)

        self.preset_combo.configure(values=list(self._preset_key_by_label.keys()))

        # Scene rule rows (Scene Rules tab) each carry their own preset
        # dropdown, built before this refresh could have known about a
        # profile created afterward (New/Duplicate/Import) - keep them
        # in sync too, without disturbing whatever's already selected.
        for row_data in getattr(self, "scene_rule_rows", []):
            row_data["preset_combo"].configure(values=self._preset_labels_for_rules())

    def _select_profile_by_key(self, key):
        for label, candidate_key in self._preset_key_by_label.items():
            if candidate_key == key:
                self.preset_var.set(label)
                self._display_preset_phrases(key)
                return

    def _prompt_profile_name(self, title, initial=""):
        built_in_labels = {preset["label"] for preset in presets.PRESETS.values()}

        while True:
            name = simpledialog.askstring(title, "Profile name:", initialvalue=initial, parent=self.parent)
            if name is None:
                return None

            name = name.strip()

            if not name:
                messagebox.showerror("Invalid name", "Profile name can't be empty.")
                continue

            if name in built_in_labels:
                messagebox.showerror(
                    "Invalid name", f'"{name}" is a built-in preset name - pick something else.'
                )
                continue

            if name in self.config.get("custom_profiles", {}):
                if not messagebox.askyesno(
                    "Already exists", f'A profile named "{name}" already exists. Overwrite it?'
                ):
                    continue

            return name

    def _create_or_replace_profile(self, name, fields):
        config = app_config.load_config()
        config["custom_profiles"] = dict(config.get("custom_profiles", {}))
        config["custom_profiles"][name] = fields
        app_config.save_config(config)

        self.config = config
        self._refresh_preset_dropdown_values()

    def _new_profile(self):
        name = self._prompt_profile_name("New Profile")
        if not name:
            return

        self._create_or_replace_profile(name, presets.blank_custom_profile())
        self._select_profile_by_key(presets.make_custom_key(name))

    def _duplicate_profile(self):
        source_key = self._current_preset_key()
        source_preset = presets.get_preset(source_key, self.config.get("custom_profiles", {}))

        name = self._prompt_profile_name("Duplicate Profile", initial=f"{source_preset['label']} (copy)")
        if not name:
            return

        fields = {k: v for k, v in source_preset.items() if k != "label"}
        self._create_or_replace_profile(name, fields)
        self._select_profile_by_key(presets.make_custom_key(name))

    def _delete_profile(self):
        selected_key = self._current_preset_key()

        if not presets.is_custom_key(selected_key):
            messagebox.showerror("Can't delete", "Built-in presets can't be deleted.")
            return

        name = presets.custom_profile_name(selected_key)
        if not messagebox.askyesno("Delete profile", f'Delete the custom profile "{name}"? This can\'t be undone.'):
            return

        config = app_config.load_config()
        config["custom_profiles"] = dict(config.get("custom_profiles", {}))
        config["custom_profiles"].pop(name, None)

        if config.get("preset") == selected_key:
            config["preset"] = presets.DEFAULT_PRESET

        app_config.save_config(config)
        self.config = config
        self._refresh_preset_dropdown_values()
        self._select_profile_by_key(config["preset"])

    def _export_profile(self):
        selected_key = self._current_preset_key()
        preset = presets.get_preset(selected_key, self.config.get("custom_profiles", {}))

        # Export whatever's currently showing in the fields, so in-progress
        # edits to a custom profile can be exported before they're saved.
        if presets.is_custom_key(selected_key):
            fields = self._collect_profile_fields()
        else:
            fields = dict(preset)

        default_name = preset["label"].replace("/", "-").strip() + ".json"
        path = filedialog.asksaveasfilename(
            title="Export preset",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return

        payload = {"name": preset["label"]}
        for field, _label in PHRASE_LIST_FIELDS:
            payload[field] = fields.get(field, [])
        payload["pause_end_pattern"] = fields.get("pause_end_pattern", "")
        payload["whisper_initial_prompt"] = fields.get("whisper_initial_prompt", "")

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return

        messagebox.showinfo("Exported", f"Saved to:\n{path}")

    def _import_profile(self):
        path = filedialog.askopenfilename(title="Import preset", filetypes=[("JSON", "*.json")])
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                payload = json.load(f)
        except Exception as exc:
            messagebox.showerror("Import failed", f"Couldn't read that file:\n{exc}")
            return

        name = self._prompt_profile_name("Import Profile", initial=str(payload.get("name", "Imported Profile")))
        if not name:
            return

        fields = {}
        for field, _label in PHRASE_LIST_FIELDS:
            value = payload.get(field, [])
            fields[field] = [str(v) for v in value] if isinstance(value, list) else []
        fields["pause_end_pattern"] = str(payload.get("pause_end_pattern", ""))
        fields["whisper_initial_prompt"] = str(payload.get("whisper_initial_prompt", ""))

        self._create_or_replace_profile(name, fields)
        self._select_profile_by_key(presets.make_custom_key(name))

    # -----------------------------------------------------------
    # Save
    # -----------------------------------------------------------

    def _save(self):
        selected_key = self._current_preset_key()

        # Re-read from disk rather than starting from self.config (a
        # snapshot from whenever this tab was built) - other parts of
        # the app (e.g. the Run tab's Auto Verify/Auto Render checkboxes)
        # can write to the same config file independently, and starting
        # from a stale snapshot here would silently revert those.
        config = app_config.load_config()
        config["theme"] = self._current_theme_key()
        config["preset"] = selected_key
        config["obs_host"] = self.obs_host_var.get().strip()
        config["recording_folder"] = self.recording_folder_var.get().strip()
        config["output_folder"] = self.output_folder_var.get().strip()
        config["full_transcript_srt_folder"] = self.full_transcript_srt_folder_var.get().strip()
        config["audio_source_type"] = AUDIO_SOURCE_TYPE_KEY_BY_LABEL.get(
            self.audio_source_type_var.get(), "loopback"
        )
        config["audio_device_name"] = self.audio_device_name_var.get().strip()
        config["whisper_model"] = self.whisper_model_var.get().strip()
        config["whisper_device"] = self.whisper_device_var.get().strip()
        config["whisper_compute_type"] = self.whisper_compute_type_var.get().strip()
        config["verify_model"] = self.verify_model_var.get().strip()
        config["verify_device"] = self.verify_device_var.get().strip()
        config["verify_compute_type"] = self.verify_compute_type_var.get().strip()

        config["remote_api_enabled"] = self.remote_api_enabled_var.get()
        config["audio_excitement_enabled"] = self.audio_excitement_enabled_var.get()

        try:
            config["obs_port"] = int(self.obs_port_var.get().strip())
            config["audio_device_fallback_index"] = int(self.audio_device_fallback_var.get().strip())
            config["remote_api_port"] = int(self.remote_api_port_var.get().strip())
            config["possible_threshold"] = int(self.possible_threshold_var.get().strip())
            config["save_threshold"] = int(self.save_threshold_var.get().strip())
            config["verified_similarity"] = int(self.verified_similarity_var.get().strip())
            config["verified_confidence"] = float(self.verified_confidence_var.get().strip())
            config["audio_excitement_moderate_ratio"] = float(self.audio_excitement_moderate_ratio_var.get().strip())
            config["audio_excitement_strong_ratio"] = float(self.audio_excitement_strong_ratio_var.get().strip())
            config["audio_excitement_moderate_bonus"] = int(self.audio_excitement_moderate_bonus_var.get().strip())
            config["audio_excitement_strong_bonus"] = int(self.audio_excitement_strong_bonus_var.get().strip())
            caption_style_key = self._current_caption_style_key()
            caption_style_fields = self._collect_caption_style_fields()
        except ValueError as exc:
            messagebox.showerror("Invalid value", f"One of the numeric fields isn't a valid number:\n{exc}")
            return

        if presets.is_custom_key(selected_key):
            profile_name = presets.custom_profile_name(selected_key)
            config["custom_profiles"] = dict(config.get("custom_profiles", {}))
            config["custom_profiles"][profile_name] = self._collect_profile_fields()

        config["render_style"] = self._current_render_style_key()
        config["apply_vertical_layout"] = self.apply_vertical_layout_var.get()
        config["render_encoder"] = RENDER_ENCODER_KEY_BY_LABEL.get(
            self.render_encoder_var.get(), "nvenc"
        )
        config["burn_in_captions"] = self.burn_in_captions_var.get()

        config["caption_style"] = caption_style_key
        if caption_style_key == "custom":
            config["custom_caption_style"] = caption_style_fields

        config["scene_rules"] = self._collect_scene_rules()

        for field, label in (
            ("recording_folder", "Recording folder"),
            ("output_folder", "Output folder"),
            ("full_transcript_srt_folder", "Full-session SRT folder"),
        ):
            value = config.get(field, "")
            if value and app_config.is_inside_install_dir(value):
                messagebox.showerror(
                    "Invalid folder",
                    f'"{label}" is set to the app\'s own install folder:\n\n{value}\n\n'
                    "That folder isn't writable without admin rights and isn't where "
                    "your recordings live - pick a different folder (e.g. inside your "
                    "user folder) and save again.",
                )
                return

        new_password = self.obs_password_var.get()
        if new_password:
            app_config.set_obs_password(new_password)
            self.obs_password_var.set("")

        app_config.save_config(config)
        self.config = config
        messagebox.showinfo("Saved", "Settings saved.")


def main():
    root = tk.Tk()
    root.title("OBS AI Highlights — Settings")
    root.geometry("760x680")
    root.minsize(700, 600)

    try:
        root.iconbitmap(str(app_config.resource_path("icon.ico")))
    except Exception:
        pass

    ui_theme.apply_theme(ui_theme.get_theme())

    SettingsUI(root)

    root.mainloop()


if __name__ == "__main__":
    main()
