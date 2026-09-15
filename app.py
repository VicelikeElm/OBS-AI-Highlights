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
from version import APP_VERSION, GITHUB_REPO

WORKER_LABELS = {
    "capture": "Highlight Capture",
    "verify": "Verify Clips",
    "render": "Render Clips",
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

        self.active_process = None
        self.active_role = None
        self.log_queue = queue.Queue()
        self.update_queue = queue.Queue()
        self.download_queue = queue.Queue()
        self._latest_release_url = None
        self._latest_asset_url = None
        self._latest_asset_size = None

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)

        run_tab = ttk.Frame(notebook, padding=12)
        settings_tab = ttk.Frame(notebook, padding=12)
        updates_tab = ttk.Frame(notebook, padding=12)

        notebook.add(run_tab, text="Run")
        notebook.add(settings_tab, text="Settings")
        notebook.add(updates_tab, text="Updates")

        self._build_run_tab(run_tab)
        self.settings_ui = SettingsUI(settings_tab)
        self._build_updates_tab(updates_tab)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_log_queue()
        self._check_for_updates()

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
            background="#111111",
            foreground="#DDDDDD",
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
