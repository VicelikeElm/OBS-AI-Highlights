# -*- coding: utf-8 -*-
"""OBS AI Highlights - unified app.

Single entry point, whether run from source or packaged as one frozen
exe. With no arguments it opens the GUI (Run tab + embedded Settings
tab). With `--worker <role>` it acts as a worker process instead,
running one of the three engine scripts' main() and exiting when done -
this is how the GUI launches live capture / verification / rendering,
and how highlight_engine.py's own post-service auto-chaining relaunches
itself once frozen (see config.worker_launch_command()).

    python app.py                  # GUI
    python app.py --worker capture # runs highlight_engine.main()
    python app.py --worker verify  # runs verify_clips.main()
    python app.py --worker render  # runs render_clips.main()
"""

import os
import sys
import json
import queue
import tempfile
import threading
import subprocess
import urllib.request
import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

try:
    import psutil
except Exception:
    psutil = None

import config as app_config
import clip_manager
import ui_theme
import session_stats
from settings_ui import SettingsUI
from version import APP_VERSION, GITHUB_REPO

WORKER_LABELS = {
    "capture": "Highlight Capture",
    "verify": "Verify Clips",
    "render": "Render Clips",
}

CLIP_STATUS_FILTER_VALUES = ["Pending", "Review", "Verified", "Rejected"]

CLIP_SORT_KEY_FUNCS = {
    "clip": lambda clip: clip["base_name"],
    "score": lambda clip: clip["score"] if clip["score"] is not None else -1,
    "status": lambda clip: clip["status"],
    "duration": lambda clip: clip["duration"] if clip["duration"] is not None else -1,
    "rendered": lambda clip: clip["rendered"],
}


def _parse_version(text):
    """"v1.2.3" / "1.2.3" -> (1, 2, 3), tolerant of stray characters so a
    malformed tag never crashes the comparison, just sorts low."""
    text = text.strip().lstrip("vV")
    parts = []

    for piece in text.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)

    return tuple(parts)


def _fetch_latest_release():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "OBS-AI-Highlights",
        },
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))

    tag_name = data.get("tag_name", "")
    html_url = data.get("html_url", "")

    asset_url = None
    asset_size = None

    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if name.startswith("OBSAIHighlights-Setup-") and name.endswith(".exe"):
            asset_url = asset.get("browser_download_url")
            asset_size = asset.get("size")
            break

    return tag_name, html_url, asset_url, asset_size


def _download_installer(asset_url, expected_size, progress_callback):
    request = urllib.request.Request(
        asset_url,
        headers={"User-Agent": "OBS-AI-Highlights"},
    )

    dest_path = Path(tempfile.gettempdir()) / Path(asset_url).name

    with urllib.request.urlopen(request, timeout=30) as response:
        downloaded = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                progress_callback(downloaded, expected_size)

    if expected_size and dest_path.stat().st_size != expected_size:
        raise RuntimeError("Downloaded file size doesn't match - it may be corrupted.")

    return dest_path


def run_worker(role):
    if role == "capture":
        import highlight_engine
        highlight_engine.main()
    elif role == "verify":
        import verify_clips
        verify_clips.main()
    elif role == "render":
        import render_clips
        render_clips.main()
    else:
        raise SystemExit(f"Unknown worker role: {role}")


class MainApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"OBS AI Highlights v{APP_VERSION}")
        self.root.geometry("880x700")
        self.root.minsize(780, 600)

        self._text_widget_colors = ui_theme.text_widget_colors(ui_theme.get_theme())

        self.active_process = None
        self.active_role = None
        self.log_queue = queue.Queue()
        self.update_queue = queue.Queue()
        self.download_queue = queue.Queue()
        self.clip_render_queue = queue.Queue()
        self._encoder_fallback_notice_shown = False
        self._latest_release_url = None
        self._latest_asset_url = None
        self._latest_asset_size = None

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)

        run_tab = ttk.Frame(notebook, padding=12)
        clips_tab = ttk.Frame(notebook, padding=12)
        sessions_tab = ttk.Frame(notebook, padding=12)
        settings_tab = ttk.Frame(notebook, padding=12)
        updates_tab = ttk.Frame(notebook, padding=12)

        notebook.add(run_tab, text="Run")
        notebook.add(clips_tab, text="Clips")
        notebook.add(sessions_tab, text="Sessions")
        notebook.add(settings_tab, text="Settings")
        notebook.add(updates_tab, text="Updates")

        self._build_run_tab(run_tab)
        self._build_clips_tab(clips_tab)
        self._build_sessions_tab(sessions_tab)
        self.settings_ui = SettingsUI(settings_tab, on_theme_change=self._apply_theme_to_text_widgets)
        self._build_updates_tab(updates_tab)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_log_queue()
        self._poll_clip_render_queue()
        self._check_for_updates()

        # See _apply_theme_to_text_widgets()'s own docstring for why this
        # re-application (not just the colors each Text widget was
        # constructed with) is needed.
        self._apply_theme_to_text_widgets(ui_theme.get_theme())

    # -----------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------

    def _build_run_tab(self, parent):
        button_row = ttk.Frame(parent)
        button_row.pack(fill="x", pady=(0, 10))

        self.capture_button = ttk.Button(
            button_row,
            text="Start Highlight Capture",
            command=lambda: self._start_worker("capture"),
        )
        self.capture_button.pack(side="left")

        self.stop_button = ttk.Button(
            button_row,
            text="Stop",
            command=self._stop_worker,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=(8, 0))

        self.verify_button = ttk.Button(
            button_row,
            text="Verify Clips",
            command=lambda: self._start_worker("verify"),
        )
        self.verify_button.pack(side="left", padx=(20, 0))

        self.render_button = ttk.Button(
            button_row,
            text="Render Clips",
            command=lambda: self._start_worker("render"),
        )
        self.render_button.pack(side="left", padx=(8, 0))

        pipeline_config = app_config.load_config()

        self.auto_verify_var = tk.BooleanVar(value=bool(pipeline_config.get("auto_verify", True)))
        self.auto_render_var = tk.BooleanVar(value=bool(pipeline_config.get("auto_render", True)))

        pipeline_row = ttk.Frame(parent)
        pipeline_row.pack(fill="x", pady=(0, 10))

        ttk.Checkbutton(
            pipeline_row,
            text="Auto Verify after capture",
            variable=self.auto_verify_var,
            command=self._save_pipeline_settings,
        ).pack(side="left")

        ttk.Checkbutton(
            pipeline_row,
            text="Auto Render after verify (moves finished clips to Ready)",
            variable=self.auto_render_var,
            command=self._save_pipeline_settings,
        ).pack(side="left", padx=(16, 0))

        self.status_label = ttk.Label(parent, text="Idle.")
        self.status_label.pack(fill="x", pady=(0, 6))

        log_frame = ttk.Frame(parent)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            log_frame,
            state="disabled",
            wrap="word",
            **self._text_widget_colors,
        )
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _save_pipeline_settings(self):
        # Read-modify-write against the current on-disk config rather
        # than any in-memory copy, same reasoning as SettingsUI._save()'s
        # own fresh-read fix - this and the Settings tab both write to
        # the same file independently.
        config = app_config.load_config()
        config["auto_verify"] = self.auto_verify_var.get()
        config["auto_render"] = self.auto_render_var.get()
        app_config.save_config(config)

    def _build_sessions_tab(self, parent):
        ttk.Label(
            parent,
            text=(
                "Each Highlight Capture run is a session - browse detection stats to tune "
                "presets against real data instead of guessing."
            ),
            wraplength=680,
        ).pack(anchor="w", pady=(0, 8))

        list_frame = ttk.Frame(parent)
        list_frame.pack(fill="both", expand=True)

        columns = ("label", "started", "thoughts", "saved")
        self.sessions_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=8)
        self.sessions_tree.heading("label", text="Session")
        self.sessions_tree.heading("started", text="Started")
        self.sessions_tree.heading("thoughts", text="Thoughts")
        self.sessions_tree.heading("saved", text="Saved")
        self.sessions_tree.column("label", width=260)
        self.sessions_tree.column("started", width=140)
        self.sessions_tree.column("thoughts", width=80, anchor="center")
        self.sessions_tree.column("saved", width=80, anchor="center")
        self.sessions_tree.pack(side="left", fill="both", expand=True)

        tree_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.sessions_tree.yview)
        self.sessions_tree.configure(yscrollcommand=tree_scrollbar.set)
        tree_scrollbar.pack(side="left", fill="y")

        self.sessions_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_session_selected())

        button_row = ttk.Frame(parent)
        button_row.pack(fill="x", pady=(8, 8))

        ttk.Button(button_row, text="Refresh", command=self._refresh_sessions_list).pack(side="left")
        ttk.Button(button_row, text="Rename...", command=self._rename_selected_session).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(button_row, text="Delete", command=self._delete_selected_session).pack(
            side="left", padx=(6, 0)
        )

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=(0, 8))

        self.session_detail_text = tk.Text(
            parent,
            height=14,
            state="disabled",
            wrap="word",
            **self._text_widget_colors,
        )
        self.session_detail_text.pack(fill="both", expand=True)

        self._session_id_by_iid = {}
        self._refresh_sessions_list()

    def _refresh_sessions_list(self):
        for row in self.sessions_tree.get_children():
            self.sessions_tree.delete(row)

        self._session_id_by_iid = {}

        for record in session_stats.list_sessions():
            started = record.get("started_at", "")[:16].replace("T", " ")
            iid = self.sessions_tree.insert(
                "",
                "end",
                values=(
                    record.get("label", ""),
                    started,
                    record.get("thoughts_analyzed", 0),
                    record.get("saved_count", 0),
                ),
            )
            self._session_id_by_iid[iid] = record["session_id"]

        self._clear_session_detail()

    def _selected_session_id(self):
        selection = self.sessions_tree.selection()
        if not selection:
            return None
        return self._session_id_by_iid.get(selection[0])

    def _on_session_selected(self):
        session_id = self._selected_session_id()
        if not session_id:
            self._clear_session_detail()
            return

        record = session_stats.get_session(session_id)
        if not record:
            self._clear_session_detail()
            return

        self._show_session_detail(record)

    def _show_session_detail(self, record):
        lines = []
        lines.append(f"Session: {record.get('label', '')}")
        lines.append(f"Preset: {record.get('preset', '')}")
        lines.append(f"Started: {record.get('started_at', '')}")
        lines.append(f"Ended: {record.get('ended_at') or '(still running, or ended abnormally)'}")
        lines.append("")
        lines.append(f"Thoughts analyzed: {record.get('thoughts_analyzed', 0)}")
        lines.append(f"  Ignored:  {record.get('ignored_count', 0)}")
        lines.append(f"  Possible: {record.get('possible_count', 0)}")
        lines.append(f"  Saved:    {record.get('saved_count', 0)}")
        lines.append(f"Average score: {session_stats.average_score(record):.1f}")
        lines.append("")

        lines.append("Phrases most responsible (drove a score up):")
        top = session_stats.top_phrases(record, key="phrase_hit_counts")
        if top:
            for phrase, count in top:
                lines.append(f"  {count:>3}x  {phrase}")
        else:
            lines.append("  (none)")
        lines.append("")

        lines.append("Rejected/admin phrases (dragged a score down):")
        top_admin = session_stats.top_phrases(record, key="admin_phrase_hit_counts")
        if top_admin:
            for phrase, count in top_admin:
                lines.append(f"  {count:>3}x  {phrase}")
        else:
            lines.append("  (none)")
        lines.append("")

        clips = record.get("saved_clips", [])
        lines.append(f"Saved clips ({len(clips)}):")
        for clip in clips:
            lines.append(f"  {clip}")

        self.session_detail_text.configure(state="normal")
        self.session_detail_text.delete("1.0", "end")
        self.session_detail_text.insert("1.0", "\n".join(lines))
        self.session_detail_text.configure(state="disabled")

    def _clear_session_detail(self):
        self.session_detail_text.configure(state="normal")
        self.session_detail_text.delete("1.0", "end")
        self.session_detail_text.configure(state="disabled")

    def _rename_selected_session(self):
        session_id = self._selected_session_id()
        if not session_id:
            return

        record = session_stats.get_session(session_id)
        if not record:
            return

        new_label = simpledialog.askstring(
            "Rename Session", "New name:", initialvalue=record.get("label", ""), parent=self.root
        )
        if not new_label:
            return

        session_stats.rename_session(session_id, new_label.strip())
        self._refresh_sessions_list()

    def _delete_selected_session(self):
        session_id = self._selected_session_id()
        if not session_id:
            return

        record = session_stats.get_session(session_id)
        if not record:
            return

        if not messagebox.askyesno(
            "Delete session",
            f'Delete stats for "{record.get("label", "")}"?\n\n'
            "This only removes the stats record - it does not delete the actual clip files.",
        ):
            return

        session_stats.delete_session(session_id)
        self._refresh_sessions_list()

    def _build_clips_tab(self, parent):
        ttk.Label(
            parent,
            text=(
                "Every detected clip, across every pipeline stage - play it, approve or reject "
                "a Review clip, render a Verified one on demand, or delete it outright. "
                "Ctrl+click or Shift+click to select several at once and act on them together. "
                "Click a column header to sort."
            ),
            wraplength=680,
        ).pack(anchor="w", pady=(0, 8))

        filter_row = ttk.Frame(parent)
        filter_row.pack(fill="x", pady=(0, 6))

        ttk.Label(filter_row, text="Status:").pack(side="left")

        self.clip_status_filter_var = tk.StringVar(value="All")
        self.clip_status_filter_combo = ttk.Combobox(
            filter_row,
            textvariable=self.clip_status_filter_var,
            values=["All"] + CLIP_STATUS_FILTER_VALUES,
            state="readonly",
            width=12,
        )
        self.clip_status_filter_combo.pack(side="left", padx=(8, 0))
        self.clip_status_filter_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._refresh_clips_list()
        )

        list_frame = ttk.Frame(parent)
        list_frame.pack(fill="both", expand=True)

        columns = ("clip", "score", "status", "duration", "rendered")
        self._clip_column_labels = {
            "clip": "Clip",
            "score": "Score",
            "status": "Status",
            "duration": "Duration",
            "rendered": "Rendered",
        }
        self._clips_sort_key = None
        self._clips_sort_reverse = False

        self.clips_tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", height=8, selectmode="extended"
        )
        for column in columns:
            self.clips_tree.heading(
                column,
                text=self._clip_column_labels[column],
                command=lambda c=column: self._sort_clips_by(c),
            )
        self.clips_tree.column("clip", width=220)
        self.clips_tree.column("score", width=60, anchor="center")
        self.clips_tree.column("status", width=90, anchor="center")
        self.clips_tree.column("duration", width=80, anchor="center")
        self.clips_tree.column("rendered", width=80, anchor="center")
        self.clips_tree.pack(side="left", fill="both", expand=True)

        tree_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.clips_tree.yview)
        self.clips_tree.configure(yscrollcommand=tree_scrollbar.set)
        tree_scrollbar.pack(side="left", fill="y")

        self.clips_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_clip_selected())

        button_row = ttk.Frame(parent)
        button_row.pack(fill="x", pady=(8, 8))

        ttk.Button(button_row, text="Refresh", command=self._refresh_clips_list).pack(side="left")

        self.clip_play_button = ttk.Button(button_row, text="Play", command=self._play_selected_clip)
        self.clip_play_button.pack(side="left", padx=(6, 0))

        self.clip_approve_button = ttk.Button(
            button_row, text="Approve", command=self._approve_selected_clip
        )
        self.clip_approve_button.pack(side="left", padx=(6, 0))

        self.clip_reject_button = ttk.Button(
            button_row, text="Reject", command=self._reject_selected_clip
        )
        self.clip_reject_button.pack(side="left", padx=(6, 0))

        self.clip_render_button = ttk.Button(
            button_row, text="Render", command=self._render_selected_clip
        )
        self.clip_render_button.pack(side="left", padx=(6, 0))

        self.clip_delete_button = ttk.Button(
            button_row, text="Delete", command=self._delete_selected_clip
        )
        self.clip_delete_button.pack(side="left", padx=(6, 0))

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=(0, 8))

        self.clip_detail_text = tk.Text(
            parent,
            height=14,
            state="disabled",
            wrap="word",
            **self._text_widget_colors,
        )
        self.clip_detail_text.pack(fill="both", expand=True)

        self._clip_by_iid = {}
        self._refresh_clips_list()

    def _sort_clips_by(self, column):
        if self._clips_sort_key == column:
            self._clips_sort_reverse = not self._clips_sort_reverse
        else:
            self._clips_sort_key = column
            self._clips_sort_reverse = False

        self._refresh_clips_list()

    def _refresh_clips_list(self):
        for row in self.clips_tree.get_children():
            self.clips_tree.delete(row)

        self._clip_by_iid = {}

        clips = clip_manager.list_clips()

        status_filter = self.clip_status_filter_var.get()
        if status_filter != "All":
            clips = [clip for clip in clips if clip["status"] == status_filter]

        if self._clips_sort_key:
            clips.sort(
                key=CLIP_SORT_KEY_FUNCS[self._clips_sort_key],
                reverse=self._clips_sort_reverse,
            )

        for column, label in self._clip_column_labels.items():
            indicator = ""
            if column == self._clips_sort_key:
                indicator = " ▼" if self._clips_sort_reverse else " ▲"
            self.clips_tree.heading(column, text=label + indicator)

        for clip in clips:
            iid = self.clips_tree.insert(
                "",
                "end",
                values=(
                    clip_manager.format_clip_label(clip["base_name"]),
                    clip["score"] if clip["score"] is not None else "",
                    clip["status"],
                    f"{clip['duration']:.1f}s" if clip["duration"] is not None else "",
                    "Yes" if clip["rendered"] else "No",
                ),
            )
            self._clip_by_iid[iid] = clip["base_name"]

        self._clear_clip_detail()

    def _selected_clip_base_names(self):
        return [
            self._clip_by_iid[iid] for iid in self.clips_tree.selection() if iid in self._clip_by_iid
        ]

    def _on_clip_selected(self):
        base_names = self._selected_clip_base_names()

        if not base_names:
            self._clear_clip_detail()
            return

        if len(base_names) == 1:
            self._show_clip_detail(clip_manager.get_clip(base_names[0]))
            return

        self.clip_detail_text.configure(state="normal")
        self.clip_detail_text.delete("1.0", "end")
        self.clip_detail_text.insert("1.0", f"{len(base_names)} clips selected.")
        self.clip_detail_text.configure(state="disabled")

    def _show_clip_detail(self, clip):
        lines = []
        lines.append(f"Clip: {clip_manager.format_clip_label(clip['base_name'])}")
        lines.append(f"Status: {clip['status']}")
        lines.append(f"Score: {clip['score'] if clip['score'] is not None else '(unknown)'}")
        duration = clip["duration"]
        lines.append(f"Duration: {f'{duration:.1f}s' if duration is not None else '(unknown)'}")
        lines.append(f"Rendered: {'Yes' if clip['rendered'] else 'No'}")
        lines.append("")

        lines.append("Detection reasons:")
        if clip["reasons"]:
            for reason in clip["reasons"]:
                lines.append(f"  - {reason}")
        else:
            lines.append("  (none recorded)")
        lines.append("")

        lines.append("Transcript:")
        lines.append(clip["transcript"] or "(none)")

        self.clip_detail_text.configure(state="normal")
        self.clip_detail_text.delete("1.0", "end")
        self.clip_detail_text.insert("1.0", "\n".join(lines))
        self.clip_detail_text.configure(state="disabled")

    def _clear_clip_detail(self):
        self.clip_detail_text.configure(state="normal")
        self.clip_detail_text.delete("1.0", "end")
        self.clip_detail_text.configure(state="disabled")

    def _play_selected_clip(self):
        base_names = self._selected_clip_base_names()
        if not base_names:
            return

        if len(base_names) > 1:
            messagebox.showinfo("Play", "Select exactly one clip to play.")
            return

        ok, message = clip_manager.play_clip(base_names[0])
        if not ok:
            messagebox.showerror("Play", message)

    def _report_bulk_result(self, action_label, results):
        """A single clip keeps the old, silent-on-success behavior (only
        an error dialog on failure) - multi-select bulk actions always
        show a short summary, since silently skipping some of several
        selected clips (e.g. approving 3 Review clips when one is
        already Verified) would otherwise be invisible."""
        failed = [result for result in results if not result[1]]

        if len(results) == 1:
            if failed:
                messagebox.showerror(action_label, failed[0][2])
            return

        succeeded_count = len(results) - len(failed)
        lines = [f"{action_label}: {succeeded_count} of {len(results)} clip(s) succeeded."]

        if failed:
            lines.append("")
            lines.append("Not applied to:")
            for base_name, _ok, message in failed[:8]:
                lines.append(f"  - {clip_manager.format_clip_label(base_name)}: {message}")
            if len(failed) > 8:
                lines.append(f"  ...and {len(failed) - 8} more.")

            messagebox.showwarning(action_label, "\n".join(lines))
        else:
            messagebox.showinfo(action_label, "\n".join(lines))

    def _bulk_clip_action(self, action_label, action_fn):
        base_names = self._selected_clip_base_names()
        if not base_names:
            return

        results = [(base_name, *action_fn(base_name)) for base_name in base_names]

        self._report_bulk_result(action_label, results)
        self._refresh_clips_list()

    def _approve_selected_clip(self):
        self._bulk_clip_action("Approve", clip_manager.approve_clip)

    def _reject_selected_clip(self):
        self._bulk_clip_action("Reject", clip_manager.reject_clip)

    def _render_selected_clip(self):
        base_names = self._selected_clip_base_names()
        if not base_names:
            return

        self.clip_approve_button.configure(state="disabled")
        self.clip_reject_button.configure(state="disabled")
        self.clip_render_button.configure(state="disabled", text="Rendering...")
        self.clip_delete_button.configure(state="disabled")

        threading.Thread(target=self._render_clips_worker, args=(base_names,), daemon=True).start()

    def _render_clips_worker(self, base_names):
        results = []

        for index, base_name in enumerate(base_names, start=1):
            if len(base_names) > 1:
                self.clip_render_queue.put(("progress", index, len(base_names)))

            try:
                ok, message = clip_manager.render_clip(base_name)
            except Exception as error:
                ok, message = False, f"Unexpected error: {error}"

            results.append((base_name, ok, message))

        self.clip_render_queue.put(("done", results))

    def _poll_clip_render_queue(self):
        try:
            while True:
                item = self.clip_render_queue.get_nowait()

                if item[0] == "progress":
                    _tag, index, total = item
                    self.clip_render_button.configure(text=f"Rendering {index}/{total}...")
                    continue

                _tag, results = item

                self.clip_approve_button.configure(state="normal")
                self.clip_reject_button.configure(state="normal")
                self.clip_render_button.configure(state="normal", text="Render")
                self.clip_delete_button.configure(state="normal")

                self._report_bulk_result("Render", results)
                self._notify_encoder_fallback_once()
                self._refresh_clips_list()
        except queue.Empty:
            pass

        self.root.after(100, self._poll_clip_render_queue)

    def _apply_theme_to_text_widgets(self, theme):
        """sv_ttk retheming every ttk widget happens on its own the moment
        SettingsUI calls ui_theme.apply_theme() - this only has to catch
        the few plain tk.Text widgets sv_ttk can't reach (see
        ui_theme.py's own docstring).

        sv_ttk's own <<ThemeChanged>> handling (tk_setPalette, in its
        bundled sv.tcl) also walks and recolors every existing plain-tk
        widget - including these same Text widgets, clobbering whatever
        was just set here - but via a queued event, not synchronously,
        so exactly when that lands relative to this call isn't reliable
        to depend on. Applying now (best-effort, avoids a flash of the
        wrong colors) and again via after_idle (once every already-
        queued Tk event, that clobbering included, has drained)
        guarantees this is the last word regardless of that timing."""
        self._text_widget_colors = ui_theme.text_widget_colors(theme)

        def _apply():
            for widget in (self.log_text, self.session_detail_text, self.clip_detail_text):
                widget.configure(**self._text_widget_colors)

        _apply()
        self.root.after_idle(_apply)

    def _notify_encoder_fallback_once(self):
        """A render triggered from the Clips tab runs in-process (no
        console for a --windowed build to print render_clips.py's own
        fallback warning to), so without this, NVENC silently failing
        and falling back to CPU would just look like an unexplained
        slowdown. Shown at most once per app run, not every render."""
        if self._encoder_fallback_notice_shown:
            return

        if not clip_manager.encoder_fallback_active():
            return

        self._encoder_fallback_notice_shown = True

        messagebox.showinfo(
            "Render encoder",
            "NVENC isn't available on this machine (no NVIDIA GPU, or the driver is too "
            "old), so rendering is using CPU/software encoding instead - it'll work, just "
            "slower. Switch 'Render encoder' to CPU / Software on the Settings tab's Video "
            "Style page to skip this check on future renders.",
        )

    def _delete_selected_clip(self):
        base_names = self._selected_clip_base_names()
        if not base_names:
            return

        if len(base_names) == 1:
            prompt = (
                f"Permanently delete \"{clip_manager.format_clip_label(base_names[0])}\"?\n\n"
                "This removes the raw video, its transcript, any Verified/Review record, "
                "and a rendered Short if one exists. This cannot be undone."
            )
        else:
            prompt = (
                f"Permanently delete {len(base_names)} clips?\n\n"
                "This removes each clip's raw video, transcript, any Verified/Review record, "
                "and a rendered Short if one exists. This cannot be undone."
            )

        if not messagebox.askyesno("Delete clip" if len(base_names) == 1 else "Delete clips", prompt):
            return

        self._bulk_clip_action("Delete", clip_manager.delete_clip)

    def _build_updates_tab(self, parent):
        ttk.Label(
            parent,
            text=f"Current version: v{APP_VERSION}",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        self.update_status_label = ttk.Label(parent, text="")
        self.update_status_label.pack(anchor="w", pady=(0, 10))

        button_row = ttk.Frame(parent)
        button_row.pack(anchor="w")

        self.check_updates_button = ttk.Button(
            button_row,
            text="Check for Updates",
            command=self._check_for_updates,
        )
        self.check_updates_button.pack(side="left")

        self.update_now_button = ttk.Button(
            button_row,
            text="Update Now",
            command=self._start_update,
            state="disabled",
        )
        self.update_now_button.pack(side="left", padx=(8, 0))

        self.view_release_button = ttk.Button(
            button_row,
            text="View Release Notes",
            command=self._open_latest_release,
            state="disabled",
        )
        self.view_release_button.pack(side="left", padx=(8, 0))

        self.download_progress_label = ttk.Label(parent, text="")
        self.download_progress_label.pack(anchor="w", pady=(10, 0))

    # -----------------------------------------------------------
    # Update checking
    # -----------------------------------------------------------

    def _check_for_updates(self):
        self.check_updates_button.configure(state="disabled")
        self.update_now_button.configure(state="disabled")
        self.view_release_button.configure(state="disabled")
        self.update_status_label.configure(text="Checking...")
        self.download_progress_label.configure(text="")
        self._latest_release_url = None
        self._latest_asset_url = None
        self._latest_asset_size = None

        threading.Thread(target=self._check_for_updates_worker, daemon=True).start()
        self.root.after(100, self._poll_update_queue)

    def _check_for_updates_worker(self):
        # Push the result onto a thread-safe queue instead of calling
        # self.root.after() from this background thread directly - Tk
        # requires after() to be scheduled from the thread already
        # running the event loop, same reasoning as the log-queue
        # pattern used for worker subprocess output below.
        try:
            tag_name, html_url, asset_url, asset_size = _fetch_latest_release()
            self.update_queue.put((tag_name, html_url, asset_url, asset_size, None))
        except Exception as exc:
            self.update_queue.put((None, None, None, None, exc))

    def _poll_update_queue(self):
        try:
            tag_name, html_url, asset_url, asset_size, error = self.update_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_update_queue)
            return

        self._on_update_check_done(tag_name, html_url, asset_url, asset_size, error)

    def _on_update_check_done(self, tag_name, html_url, asset_url, asset_size, error):
        self.check_updates_button.configure(state="normal")

        if error is not None:
            self.update_status_label.configure(text=f"Couldn't check for updates: {error}")
            return

        if _parse_version(tag_name) > _parse_version(APP_VERSION):
            self.update_status_label.configure(text=f"Update available: {tag_name}")
            self._latest_release_url = html_url
            self._latest_asset_url = asset_url
            self._latest_asset_size = asset_size
            self.view_release_button.configure(state="normal")

            if asset_url:
                self.update_now_button.configure(state="normal")
            else:
                self.download_progress_label.configure(
                    text="No installer asset found on that release - use View Release Notes instead."
                )
        else:
            self.update_status_label.configure(text="You're up to date.")

    def _open_latest_release(self):
        if self._latest_release_url:
            webbrowser.open(self._latest_release_url)

    # -----------------------------------------------------------
    # Update download + install
    # -----------------------------------------------------------

    def _start_update(self):
        if not self._latest_asset_url:
            return

        if self.active_process is not None:
            self.download_progress_label.configure(
                text="Stop the current operation on the Run tab before updating."
            )
            return

        self.check_updates_button.configure(state="disabled")
        self.update_now_button.configure(state="disabled")
        self.download_progress_label.configure(text="Downloading update...")

        threading.Thread(target=self._download_update_worker, daemon=True).start()
        self.root.after(100, self._poll_download_queue)

    def _download_update_worker(self):
        try:
            def report_progress(downloaded, total):
                self.download_queue.put(("progress", downloaded, total))

            installer_path = _download_installer(
                self._latest_asset_url,
                self._latest_asset_size,
                report_progress,
            )
            self.download_queue.put(("done", installer_path, None))
        except Exception as exc:
            self.download_queue.put(("error", None, exc))

    def _poll_download_queue(self):
        try:
            kind, payload, extra = self.download_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_download_queue)
            return

        if kind == "progress":
            downloaded, total = payload, extra
            downloaded_mb = downloaded / (1024 * 1024)
            if total:
                percent = int(downloaded * 100 / total)
                total_mb = total / (1024 * 1024)
                text = f"Downloading update... {percent}% ({downloaded_mb:.1f} MB / {total_mb:.1f} MB)"
            else:
                text = f"Downloading update... {downloaded_mb:.1f} MB"
            self.download_progress_label.configure(text=text)
            self.root.after(100, self._poll_download_queue)
            return

        if kind == "done":
            installer_path = payload
            self.download_progress_label.configure(text="Download complete. Launching installer...")
            self._launch_installer_and_exit(installer_path)
            return

        error = extra
        self.download_progress_label.configure(text=f"Update failed: {error}")
        self.check_updates_button.configure(state="normal")
        self.update_now_button.configure(state="normal")

    def _launch_installer_and_exit(self, installer_path):
        try:
            subprocess.Popen([str(installer_path)])
        except Exception as exc:
            self.download_progress_label.configure(text=f"Couldn't launch installer: {exc}")
            self.check_updates_button.configure(state="normal")
            self.update_now_button.configure(state="normal")
            return

        # The installer's own CloseApplications=yes will also handle this
        # if this races, but closing proactively gives it a clean run at
        # replacing our files instead of fighting a still-open exe.
        self.root.after(500, self.root.destroy)

    # -----------------------------------------------------------
    # Worker process management
    # -----------------------------------------------------------

    def _start_worker(self, role):
        if self.active_process is not None:
            return

        self._append_log(f"--- Starting {WORKER_LABELS[role]} ---\n")
        self.status_label.configure(text=f"Running: {WORKER_LABELS[role]}")

        command = app_config.worker_launch_command(role)

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                creationflags=creationflags,
            )
        except Exception as exc:
            self._append_log(f"--- Failed to start {WORKER_LABELS[role]}: {exc} ---\n")
            self.status_label.configure(text="Idle.")
            return

        self.active_process = process
        self.active_role = role

        self.capture_button.configure(state="disabled")
        self.verify_button.configure(state="disabled")
        self.render_button.configure(state="disabled")
        self.stop_button.configure(state="normal")

        threading.Thread(target=self._read_process_output, args=(process,), daemon=True).start()

    def _read_process_output(self, process):
        try:
            for line in process.stdout:
                self.log_queue.put(line)
        finally:
            return_code = process.wait()
            self.log_queue.put(("__DONE__", return_code))

    def _poll_log_queue(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "__DONE__":
                    self._on_worker_done(item[1])
                else:
                    self._append_log(item)
        except queue.Empty:
            pass

        self.root.after(100, self._poll_log_queue)

    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_worker_done(self, return_code):
        role = self.active_role

        self._append_log(f"--- {WORKER_LABELS.get(role, role)} finished (exit code {return_code}) ---\n")
        self.status_label.configure(text="Idle.")

        self.active_process = None
        self.active_role = None

        self.capture_button.configure(state="normal")
        self.verify_button.configure(state="normal")
        self.render_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _stop_worker(self):
        if self.active_process is None:
            return

        self._append_log("--- Stop requested ---\n")
        self._kill_process_tree(self.active_process.pid)

    def _kill_process_tree(self, pid):
        """Kills a worker and everything it spawned.

        highlight_engine.py's own post-service auto-chaining means a
        "capture" worker can have a live grandchild (verify/render)
        subprocess running under it - plain terminate() only kills the
        direct child on Windows, leaving the grandchild orphaned and
        still holding GPU memory. psutil walks the whole tree instead,
        same approach already proven safe in the Sunday Service System's
        own process-check fixes."""
        if psutil is None:
            try:
                self.active_process.terminate()
            except Exception:
                pass
            return

        try:
            parent = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return

        children = parent.children(recursive=True)

        for proc in children + [parent]:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass

    def _on_close(self):
        if self.active_process is not None:
            self._kill_process_tree(self.active_process.pid)

        self.root.destroy()


def run_gui():
    root = tk.Tk()

    try:
        root.iconbitmap(str(app_config.resource_path("icon.ico")))
    except Exception:
        pass

    ui_theme.apply_theme(ui_theme.get_theme())

    MainApp(root)

    root.mainloop()


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--worker":
        run_worker(sys.argv[2])
        return

    run_gui()


if __name__ == "__main__":
    main()
