# OBS AI Highlights

Built by [Vice Media Solutions](assets/vivce_media_solutions.png).

Watches OBS's replay buffer during a live stream, uses a live Whisper
transcript to score which moments are "highlight-worthy," saves those as
clips, then re-verifies and renders them into finished vertical shorts —
no LLM calls, just a deterministic transcript heuristic you can tune per
preset.

Built-in presets change what counts as a good clip:

- **Church / Sermon** — sermon language, Scripture references, prayer-aware
  pausing
- **Gaming** — reaction phrases, clutch moments, loading-screen dead time
  filtered out
- **Twitch / Streaming** — sub/raid/hype-train moments, chat-interaction
  phrases
- **Custom profiles** — name and save as many of your own presets as you
  want (My Siege Setup, Podcast, Racing, ...), duplicated from a built-in
  or from scratch, with export/import to share them

## How it works

1. `highlight_engine.py` connects to OBS over `obsws-python`, transcribes
   the live audio with a small/fast Whisper model, and scores each moment
   against the active preset's phrase lists. When a moment scores high
   enough, it saves the OBS replay buffer to disk.
2. `verify_clips.py` re-transcribes each saved clip with a larger, more
   accurate Whisper model, cross-checks it against the live transcript
   (and optionally a full-session SRT, if you have one), and sorts clips
   into `Verified` or `Review`.
3. `render_clips.py` takes verified clips and renders them into a 1080x1920
   vertical video with burned-in captions, ready to post.

Each stage can run on its own, or all three from one app - see below.

## Easy install (Windows)

Run the installer (`OBSAIHighlights-Setup-vX.Y.Z.exe`) and launch **OBS AI
Highlights** from the Start Menu. One window, three tabs:

- **Run** — start/stop live highlight capture, with Auto Verify/Auto
  Render checkboxes controlling what happens automatically once a
  capture ends, plus manual Verify Clips/Render Clips buttons and a live
  log of what's happening.
- **Settings** — pick a preset, tune caption/render styling, set your OBS
  connection details and password, point it at your recording/output
  folders, and set up Scene Rules (e.g. a "BRB" scene pauses clipping, a
  "Gameplay" scene switches to the Gaming preset automatically).
- **Updates** — checks automatically on launch, or on demand, and can
  download and install a newer version without leaving the app.

Prerequisites the installer doesn't bundle:
- An NVIDIA GPU + driver for the default (CUDA) Whisper settings - switch
  `whisper_device`/`verify_device` to `cpu` in Settings if you don't have
  one.
- [ffmpeg](https://ffmpeg.org/download.html) on your PATH (used for the
  final vertical-video render step). The app will tell you clearly if it's
  missing rather than failing silently.

Settings live in `%APPDATA%\OBS AI Highlights\` (`highlight_config.json` +
`.env` for the OBS password) - separate from wherever the app itself is
installed, so it works without admin rights and survives a reinstall.

## Running from source

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt

python app.py                  # the unified app, same as the installed one
```

Or run any stage on its own, same as the installed app's Run tab does
internally:

```bash
python highlight_engine.py   # live capture + scoring, while OBS is streaming/recording
python verify_clips.py       # re-verify clips saved so far
python render_clips.py       # render verified clips into finished vertical shorts
```

## Remote API (Stream Deck / Companion / AutoHotkey)

While Highlight Capture is running, a small local HTTP API is available
for external tools to trigger and query it - configurable (enable/port)
in Settings → OBS & Folders. Bound to `127.0.0.1` only; it's never
reachable from the network, and it isn't available when capture isn't
running.

```
GET  /status              -> { "capturing": true, "preset": "Gaming", "paused": false, "clips_saved": 3 }
POST /highlight            -> save whatever's in the replay buffer right now, bypassing the phrase-scoring gate
POST /pause                -> suspend automatic clip-saving
POST /resume               -> resume automatic clip-saving
POST /preset/<name>        -> switch the active preset (a built-in key like "gaming", or a URL-encoded
                               "custom:<profile name>" for a named profile - same keys used in
                               highlight_config.json's "preset" field)
```

Example with `curl` (default port 8756):

```bash
curl http://127.0.0.1:8756/status
curl -X POST http://127.0.0.1:8756/highlight
curl -X POST http://127.0.0.1:8756/preset/gaming
```

## Building the Windows installer

Requires [Inno Setup 6](https://jrsoftware.org/isdl.php) and PyInstaller
(`pip install pyinstaller`, or install it into an isolated folder so it
doesn't pollute a shared venv):

```bash
python build_app_windows.py
```

Produces `dist/OBSAIHighlights/` (the packaged app) and
`installer-output/OBSAIHighlights-Setup-vX.Y.Z.exe`.

## Status

Private, early-stage companion tool - extracted from a church production
system's internal AI-shorts feature and generalized with a preset system.
Packaged as an unsigned Windows installer (no code-signing certificate
yet, so Windows SmartScreen may warn on first run).
