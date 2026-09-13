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

PHRASE_LIST_FIELDS = (
    ("strong_phrases", "Strong phrases (one per line)"),
    ("application_phrases", "Application / call-to-action phrases"),
    ("reference_phrases", "Reference phrases (Scripture, game terms, etc.)"),
    ("low_value_phrases", "Low-value phrases (penalize the score)"),
    ("pause_phrases", "Pause phrases (suspend clipping until the end pattern)"),
)


class SettingsUI:
    def __init__(self, root):
        self.root = root
        self.root.title("OBS AI Highlights — Settings")
        self.root.geometry("760x680")
        self.root.minsize(700, 600)

        self.config = app_config.load_config()

        self.preset_var = tk.StringVar()
        self.obs_host_var = tk.StringVar()
        self.obs_port_var = tk.StringVar()
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
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        preset_tab = ttk.Frame(notebook, padding=12)
        connection_tab = ttk.Frame(notebook, padding=12)
        model_tab = ttk.Frame(notebook, padding=12)

        notebook.add(preset_tab, text="Preset & Phrases")
        notebook.add(connection_tab, text="OBS & Folders")
        notebook.add(model_tab, text="Whisper & Thresholds")

        self._build_preset_tab(preset_tab)
        self._build_connection_tab(connection_tab)
        self._build_model_tab(model_tab)

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

        ttk.Label(
            parent,
            text=(
                "OBS password is never stored here - set OBS_PASSWORD in a "
                ".env file next to these scripts."
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

        self._add_labeled_entry(parent, "Audio device name", self.audio_device_name_var)
        self._add_labeled_entry(parent, "Audio device fallback index", self.audio_device_fallback_var)

    def _build_model_tab(self, parent):
        self._add_labeled_entry(parent, "Whisper model (live capture)", self.whisper_model_var)
        self._add_labeled_entry(parent, "Whisper device", self.whisper_device_var)
        self._add_labeled_entry(parent, "Whisper compute type", self.whisper_compute_type_var)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=10)

        self._add_labeled_entry(parent, "Verify model (second pass)", self.verify_model_var)
        self._add_labeled_entry(parent, "Verify device", self.verify_device_var)
        self._add_labeled_entry(parent, "Verify compute type", self.verify_compute_type_var)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=10)

        self._add_labeled_entry(parent, "Possible-clip threshold", self.possible_threshold_var)
        self._add_labeled_entry(parent, "Save-clip threshold", self.save_threshold_var)
        self._add_labeled_entry(parent, "Verified similarity", self.verified_similarity_var)
        self._add_labeled_entry(parent, "Verified confidence", self.verified_confidence_var)

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

        app_config.save_config(config)
        self.config = config
        messagebox.showinfo("Saved", "Settings saved.")


def main():
    root = tk.Tk()

    if sv_ttk is not None:
        try:
            sv_ttk.set_theme("dark")
        except Exception:
            pass

    SettingsUI(root)

    root.mainloop()


if __name__ == "__main__":
    main()
