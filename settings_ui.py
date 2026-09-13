# -*- coding: utf-8 -*-
"""Standalone settings app for the OBS AI Highlights tool.

Own Tk root, own main() - matches the standalone-app shape used
throughout the Sunday Service System (e.g. ProfileManager in
sss_profile_manager.py) but this tool has no dependency on that
codebase at all. Run directly:

    python settings_ui.py
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import sv_ttk
except Exception:
    sv_ttk = None

import config as app_config
import presets


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

PHRASE_LIST_FIELDS = (
    ("strong_phrases", "Strong phrases (one per line)"),
    ("application_phrases", "Application / call-to-action phrases"),
    ("reference_phrases", "Reference phrases (Scripture, game terms, etc.)"),
    ("low_value_phrases", "Low-value phrases (penalize the score)"),
    ("pause_phrases", "Pause phrases (suspend clipping until the end pattern)"),
)


class SettingsUI:
    def __init__(self, parent):
        self.parent = parent

        self.config = app_config.load_config()

        self.preset_var = tk.StringVar()
        self.obs_host_var = tk.StringVar()
        self.obs_port_var = tk.StringVar()
        self.obs_password_var = tk.StringVar()
        self.recording_folder_var = tk.StringVar()
        self.output_folder_var = tk.StringVar()
        self.full_transcript_srt_folder_var = tk.StringVar()
        self.audio_device_name_var = tk.StringVar()
        self.audio_device_fallback_var = tk.StringVar()
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
        self.pause_end_pattern_var = tk.StringVar()

        self.phrase_list_widgets = {}
        self.whisper_prompt_widget = None

        self._preset_key_by_label = {}
        for key, preset in presets.PRESETS.items():
            self._preset_key_by_label[preset["label"]] = key

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
        about_tab = ttk.Frame(notebook, padding=12)

        notebook.add(preset_tab, text="Preset & Phrases")
        notebook.add(connection_tab, text="OBS & Folders")
        notebook.add(model_tab, text="Whisper & Thresholds")
        notebook.add(about_tab, text="About")

        self._build_preset_tab(preset_tab)
        self._build_connection_tab(connection_tab)
        self._build_model_tab(model_tab)
        self._build_about_tab(about_tab)

        button_row = ttk.Frame(outer, padding=(0, 12, 0, 0))
        button_row.pack(fill="x")

        ttk.Button(
            button_row,
            text="Save",
            command=self._save,
        ).pack(side="right")

    def _build_preset_tab(self, parent):
        top_row = ttk.Frame(parent)
        top_row.pack(fill="x", pady=(0, 10))

        ttk.Label(top_row, text="Preset:").pack(side="left")

        preset_combo = ttk.Combobox(
            top_row,
            textvariable=self.preset_var,
            values=[preset["label"] for preset in presets.PRESETS.values()],
            state="readonly",
            width=24,
        )
        preset_combo.pack(side="left", padx=(8, 0))
        preset_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_preset_changed())

        hint = ttk.Label(
            parent,
            text=(
                "Built-in presets are read-only. Choose Custom to write your "
                "own phrase lists - they're saved and reused next time."
            ),
            wraplength=680,
            foreground="#888888",
        )
        hint.pack(fill="x", pady=(0, 10))

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
        ttk.Label(row, text="OBS password", width=32, anchor="w").pack(side="left")
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
        self._add_labeled_folder(parent, "Output folder (blank = under recording folder)", self.output_folder_var)
        self._add_labeled_folder(
            parent,
            "Full-session SRT folder (optional cross-check)",
            self.full_transcript_srt_folder_var,
        )

        self._add_audio_device_row(parent)
        self._add_labeled_entry(parent, "Audio device fallback index", self.audio_device_fallback_var)

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
            "Review.",
        )

    def _build_about_tab(self, parent):
        content = ttk.Frame(parent)
        content.place(relx=0.5, rely=0.5, anchor="center")

        logo_path = app_config.resource_path("assets", "vivce_media_solutions_ui.png")

        try:
            self._about_logo_image = tk.PhotoImage(file=str(logo_path))
            ttk.Label(content, image=self._about_logo_image).pack(pady=(0, 12))
        except Exception:
            pass

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

    def _add_labeled_combo(self, parent, label, var, values):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=32, anchor="w").pack(side="left")
        ttk.Combobox(row, textvariable=var, values=values).pack(side="left", fill="x", expand=True)

    def _add_labeled_spinbox(self, parent, label, var, from_, to, increment, fmt=None):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=32, anchor="w").pack(side="left")

        kwargs = {}
        if fmt:
            kwargs["format"] = fmt

        ttk.Spinbox(
            row,
            from_=from_,
            to=to,
            increment=increment,
            textvariable=var,
            **kwargs,
        ).pack(side="left", fill="x", expand=True)

    def _add_audio_device_row(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="Audio device (loopback)", width=32, anchor="w").pack(side="left")

        self.audio_device_combo = ttk.Combobox(row, textvariable=self.audio_device_name_var)
        self.audio_device_combo.pack(side="left", fill="x", expand=True)

        ttk.Button(
            row,
            text="Refresh",
            command=self._refresh_audio_devices,
        ).pack(side="left", padx=(6, 0))

        self._refresh_audio_devices()

    def _refresh_audio_devices(self):
        devices = _enumerate_loopback_devices()
        current = self.audio_device_name_var.get()

        self.audio_device_combo.configure(values=devices)

        # Enumerating can fail to find hardware that's actually
        # configured (unplugged, driver hiccup, etc.) - never wipe out
        # an existing saved value just because a refresh came back
        # empty or didn't include it.
        self.audio_device_name_var.set(current)

    def _add_labeled_entry(self, parent, label, var):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=32, anchor="w").pack(side="left")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)

    def _add_labeled_folder(self, parent, label, var):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=32, anchor="w").pack(side="left")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        ttk.Button(
            row,
            text="Browse…",
            command=lambda v=var: self._browse_folder(v),
        ).pack(side="left", padx=(6, 0))

    def _browse_folder(self, var):
        chosen = filedialog.askdirectory(initialdir=var.get() or ".")
        if chosen:
            var.set(chosen)

    # -----------------------------------------------------------
    # Config <-> fields
    # -----------------------------------------------------------

    def _load_config_into_fields(self):
        config = self.config

        active_key = config.get("preset", presets.DEFAULT_PRESET)
        active_preset = presets.PRESETS.get(active_key, presets.PRESETS[presets.DEFAULT_PRESET])
        self.preset_var.set(active_preset["label"])

        self.obs_host_var.set(str(config.get("obs_host", "")))
        self.obs_port_var.set(str(config.get("obs_port", "")))
        self.recording_folder_var.set(str(config.get("recording_folder", "")))
        self.output_folder_var.set(str(config.get("output_folder", "")))
        self.full_transcript_srt_folder_var.set(str(config.get("full_transcript_srt_folder", "")))
        self.audio_device_name_var.set(str(config.get("audio_device_name", "")))
        self.audio_device_fallback_var.set(str(config.get("audio_device_fallback_index", 0)))
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

        self._display_preset_phrases(active_key)

    def _on_preset_changed(self):
        selected_key = self._preset_key_by_label.get(self.preset_var.get(), presets.DEFAULT_PRESET)
        self._display_preset_phrases(selected_key)

    def _display_preset_phrases(self, preset_key):
        is_custom = preset_key == "custom"

        if is_custom:
            preset = presets.get_preset("custom", self.config.get("custom_preset"))
        else:
            preset = presets.PRESETS[preset_key]

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

    def _collect_custom_preset(self):
        custom = {}
        for field, _label in PHRASE_LIST_FIELDS:
            raw = self.phrase_list_widgets[field].get("1.0", "end")
            custom[field] = [line.strip() for line in raw.splitlines() if line.strip()]

        custom["pause_end_pattern"] = self.pause_end_pattern_var.get().strip()
        custom["whisper_initial_prompt"] = self.whisper_prompt_widget.get("1.0", "end").strip()
        return custom

    # -----------------------------------------------------------
    # Save
    # -----------------------------------------------------------

    def _save(self):
        selected_label = self.preset_var.get()
        selected_key = self._preset_key_by_label.get(selected_label, presets.DEFAULT_PRESET)

        config = dict(self.config)
        config["preset"] = selected_key
        config["obs_host"] = self.obs_host_var.get().strip()
        config["recording_folder"] = self.recording_folder_var.get().strip()
        config["output_folder"] = self.output_folder_var.get().strip()
        config["full_transcript_srt_folder"] = self.full_transcript_srt_folder_var.get().strip()
        config["audio_device_name"] = self.audio_device_name_var.get().strip()
        config["whisper_model"] = self.whisper_model_var.get().strip()
        config["whisper_device"] = self.whisper_device_var.get().strip()
        config["whisper_compute_type"] = self.whisper_compute_type_var.get().strip()
        config["verify_model"] = self.verify_model_var.get().strip()
        config["verify_device"] = self.verify_device_var.get().strip()
        config["verify_compute_type"] = self.verify_compute_type_var.get().strip()

        try:
            config["obs_port"] = int(self.obs_port_var.get().strip())
            config["audio_device_fallback_index"] = int(self.audio_device_fallback_var.get().strip())
            config["possible_threshold"] = int(self.possible_threshold_var.get().strip())
            config["save_threshold"] = int(self.save_threshold_var.get().strip())
            config["verified_similarity"] = int(self.verified_similarity_var.get().strip())
            config["verified_confidence"] = float(self.verified_confidence_var.get().strip())
        except ValueError as exc:
            messagebox.showerror("Invalid value", f"One of the numeric fields isn't a valid number:\n{exc}")
            return

        if selected_key == "custom":
            config["custom_preset"] = self._collect_custom_preset()

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

    if sv_ttk is not None:
        try:
            sv_ttk.set_theme("dark")
        except Exception:
            pass

    SettingsUI(root)

    root.mainloop()


if __name__ == "__main__":
    main()
