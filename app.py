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
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk

try:
    import sv_ttk
except Exception:
    sv_ttk = None

try:
    import psutil
except Exception:
    psutil = None

import config as app_config
from settings_ui import SettingsUI

WORKER_LABELS = {
    "capture": "Highlight Capture",
    "verify": "Verify Clips",
    "render": "Render Clips",
}


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
        self.root.title("OBS AI Highlights")
        self.root.geometry("880x700")
        self.root.minsize(780, 600)

        self.active_process = None
        self.active_role = None
        self.log_queue = queue.Queue()

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)

        run_tab = ttk.Frame(notebook, padding=12)
        settings_tab = ttk.Frame(notebook, padding=12)

        notebook.add(run_tab, text="Run")
        notebook.add(settings_tab, text="Settings")

        self._build_run_tab(run_tab)
        self.settings_ui = SettingsUI(settings_tab)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_log_queue()

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

        self.status_label = ttk.Label(parent, text="Idle.")
        self.status_label.pack(fill="x", pady=(0, 6))

        log_frame = ttk.Frame(parent)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            log_frame,
            state="disabled",
            wrap="word",
            background="#111111",
            foreground="#DDDDDD",
        )
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

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

    if sv_ttk is not None:
        try:
            sv_ttk.set_theme("dark")
        except Exception:
            pass

    MainApp(root)

    root.mainloop()


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--worker":
        run_worker(sys.argv[2])
        return

    run_gui()


if __name__ == "__main__":
    main()
