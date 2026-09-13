# OBS AI Highlights

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

Each stage is a separate script — run them side by side, or wire them
into your own pipeline.

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Requires a CUDA-capable GPU for the default Whisper settings (`device:
cuda`); switch `whisper_device`/`verify_device` to `cpu` in settings if you
don't have one.

Create a `.env` file next to these scripts with your OBS WebSocket
password (never stored in the JSON config, so it's never committed by
accident):

```
OBS_PASSWORD=your-obs-websocket-password
```

Run the settings app to pick a preset and configure folders, OBS
connection, and model settings:

```bash
python settings_ui.py
```

Settings are saved to `highlight_config.json` (git-ignored).

## Running

```bash
python highlight_engine.py   # live capture + scoring, while OBS is streaming/recording
python verify_clips.py       # re-verify clips saved so far
python render_clips.py       # render verified clips into finished vertical shorts
```

## Status

Private, early-stage companion tool — extracted from a church production
system's internal AI-shorts feature and generalized with a preset system.
Not yet packaged as a standalone installer; runs from source.
