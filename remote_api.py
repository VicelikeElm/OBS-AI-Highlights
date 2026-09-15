# -*- coding: utf-8 -*-
"""Small local HTTP API for external control (Stream Deck, Companion,
AutoHotkey, etc.) - runs only while highlight_engine.py's live capture
loop is active, on a background thread inside that same process.

Bound to 127.0.0.1 only, never configurable to any other interface -
this is a local control surface for tools running on the same machine,
not something meant to be reachable from the network.

Endpoints:
  GET  /status         -> current capture/session state as JSON
  POST /highlight       -> save whatever's in the replay buffer right
                           now, bypassing the phrase-scoring gate
  POST /pause            -> suspend automatic clip-saving
  POST /resume           -> resume automatic clip-saving
  POST /preset/<name>    -> switch the active preset (a built-in key like
                            "gaming", or "custom:<profile name>" - same
                            keys highlight_config.json's "preset" uses)

All state changes here are request FLAGS consumed by the main capture
loop on its own thread (RemoteControlState.consume_*) - this HTTP
handler thread never touches scoring globals or OBS/audio state
directly, only this lock-protected state object.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote


class RemoteControlState:
    def __init__(self):
        self._lock = threading.Lock()
        self._paused = False
        self._manual_trigger_requested = False
        self._pending_preset = None
        self._status = {}

    def set_paused(self, value):
        with self._lock:
            self._paused = bool(value)

    def is_paused(self):
        with self._lock:
            return self._paused

    def request_manual_highlight(self):
        with self._lock:
            self._manual_trigger_requested = True

    def consume_manual_highlight_request(self):
        with self._lock:
            requested = self._manual_trigger_requested
            self._manual_trigger_requested = False
            return requested

    def request_preset_switch(self, name):
        with self._lock:
            self._pending_preset = name

    def consume_preset_switch_request(self):
        with self._lock:
            name = self._pending_preset
            self._pending_preset = None
            return name

    def update_status(self, **kwargs):
        with self._lock:
            self._status.update(kwargs)

    def get_status(self):
        with self._lock:
            snapshot = dict(self._status)
            snapshot["paused"] = self._paused
            return snapshot


def _make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, payload, status_code=200):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/status":
                self._send_json(state.get_status())
            else:
                self._send_json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path == "/highlight":
                state.request_manual_highlight()
                self._send_json({"ok": True, "message": "Highlight requested."})
            elif self.path == "/pause":
                state.set_paused(True)
                self._send_json({"ok": True, "paused": True})
            elif self.path == "/resume":
                state.set_paused(False)
                self._send_json({"ok": True, "paused": False})
            elif self.path.startswith("/preset/"):
                name = unquote(self.path[len("/preset/"):])
                if not name:
                    self._send_json({"error": "missing preset name"}, 400)
                    return
                state.request_preset_switch(name)
                self._send_json({"ok": True, "requested_preset": name})
            else:
                self._send_json({"error": "not found"}, 404)

        def log_message(self, format, *args):
            pass  # don't spam the capture log with routine HTTP access lines

    return Handler


def start_server(state, port):
    """Starts the API server on a daemon thread, bound to 127.0.0.1 only.
    Returns the server (call .shutdown() to stop it), or None if the
    port couldn't be bound (e.g. already in use) - non-fatal, capture
    keeps running without remote control rather than crashing."""
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(state))
    except OSError:
        return None

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
