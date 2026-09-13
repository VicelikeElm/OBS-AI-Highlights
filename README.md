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
- **Custom** — write your own phrase lists from scratch, editable in
  `settings_ui.py`

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
Highlights** from the Start Menu. One window, two tabs:

- **Settings** — pick a preset, set your OBS connection details and
  password, and point it at your recording/output folders.
- **Run** — buttons to start/stop live highlight capture, and to manually
  re-run verification or rendering, with a live log of what's happening.

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
