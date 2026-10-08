# OBS AI Highlights

[![Latest release](https://img.shields.io/github/v/release/VicelikeElm/OBS-AI-Highlights?label=latest%20release)](https://github.com/VicelikeElm/OBS-AI-Highlights/releases/latest)

**[Download the latest Windows installer](https://github.com/VicelikeElm/OBS-AI-Highlights/releases/latest)** - grab the `.exe` from the Assets section of the latest release.

Built by [Vice Media Solutions](assets/vivce_media_solutions.png).

Watches OBS's replay buffer during a live stream, uses a live Whisper
transcript - plus a live audio-loudness signal that catches reactions
even without a matching trigger phrase - to score which moments are
"highlight-worthy," saves those as clips, then re-verifies and renders
them into finished vertical shorts. No LLM calls, no cloud audio
classifier - just deterministic transcript + loudness heuristics you can
tune per preset.

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
   vertical video with burned-in captions, ready to post. Alongside each
   finished video it also writes a ready-to-post metadata sidecar - a
   suggested title, description, hashtags, and the strongest transcript
   quote - deterministically, with no cloud AI calls.

The vertical layout and the caption burn-in are each independently
optional (Settings → Video Style) - turn either off if you'd rather
finish a clip yourself in another editor. With both off, rendering just
outputs the verified clip trimmed to its start/end point, at its
original resolution, untouched otherwise; the `.srt` caption file is
always saved in `Verified/` regardless, so you still have the caption
text/timing to use elsewhere even with burn-in off.

Video layouts (Settings → Video Style → Video layout): **Blurred
Background** (the default - the whole frame over a blurred copy of
itself), **Full Crop** (a fixed slice from the middle of the frame),
**Original (letterboxed)**, and two that look at each clip before
framing it:

- **Follow Speaker Crop** - a full-screen crop that follows the speaker
  as they move, instead of cutting a fixed slice from the middle.
- **Speaker + Lower Panel** - the speaker on top and the scene's lower
  panel (scripture, lyrics, a lower third) underneath while it's on
  screen; a full-screen follow crop the rest of the time.

Both follow the speaker by where things move on a static camera, and pan
only when the speaker has moved off-centre for several seconds. If a clip
can't be analyzed they fall back to Blurred Background, so a clip is never
skipped over a layout problem.

Each stage can run on its own, or all three from one app - see below.

## Easy install (Windows)

Run the installer (`OBSAIHighlights-Setup-vX.Y.Z.exe`) and launch **OBS AI
Highlights** from the Start Menu. One window, five tabs:

- **Run** — a quick-start checklist reminds you to start OBS Replay
  Buffer, recording, or streaming, choose a preset if needed, then start
  capture. Plain-language status messages explain what the app is doing
  and where to go next.
  Automatic clip preparation and rendering can be enabled here; manual
  actions remain available. A short, friendly activity feed shows
  important progress and warnings, while full technical details stay
  hidden until requested. Use **Check setup** to expand a checklist for the
  OBS connection, selected audio device, and recording/output folders before
  capture. Missing Tesseract is listed as optional for game-event OCR.
  The check is advisory and doesn't prevent capture from waiting for OBS
  to become active. New installations get a short setup guide, and a
  **Review clips** shortcut appears when clips are available. Empty Clips
  and Sessions views explain what to do next. The Run tab also reports a
  detected supported game
  and its profile; Rainbow Six Siege detection is included as the first
  game profile. During capture, the optional game-event engine OCRs a
  configurable primary-display region, scores recognized events and
  achievements alongside transcript/audio scores, and stores that context
  with saved clips.
- **Clips** — every detected clip in one list, across every pipeline
  stage, with its score, status, duration, and detection reasons/full
  transcript. Play a clip in your default video player, Approve or
  Reject a Review clip (non-destructive - nothing is deleted, so a
  reject is easy to undo), Render a Verified one right now instead of
  waiting for the next batch, or Delete a clip you don't want outright.
  Ctrl+click/Shift+click to select several clips and approve, reject,
  render, or delete them all at once. Filter by status and click a
  column header to sort. Selecting a clip shows a preview thumbnail and
  its trim points - override the start/end time directly if the
  auto-detected trim cut off a word or left in too much dead air, then
  re-render to see it.
- **Sessions** — every capture run is tracked as a session: how many
  moments were analyzed, how many were ignored/possible/saved, the
  average score, and which phrases were actually most responsible for
  good (or rejected) clips - useful for tuning a preset against real
  data instead of guessing. Rename or delete a session's stats record
  from here (clip files themselves aren't touched).
- **Settings** — choose a preset, connect OBS, pick recording/output
  folders, and choose a video layout from the main settings tabs. The
  **Advanced** tab groups optional controls: custom preset phrases,
  Whisper/scoring choices, game-event OCR, audio devices and local
  integrations, and Scene Rules (e.g. a "BRB" scene pauses clipping, a
  "Gameplay" scene switches to the Gaming preset automatically). Most
  users can leave these advanced options at their defaults. The Video
  Style tab shows detailed caption controls only when **Custom** is
  selected. Switch between Dark and Light themes on the About tab;
  changes apply immediately.
- **Updates** — checks automatically on launch, or on demand, and can
  download and install a newer version without leaving the app. When a
  release includes an app-only update package, the updater downloads that
  instead of the full installer; otherwise it falls back to the full
  installer.

ffmpeg is bundled with the installer (an LGPL-only static build - see
[Third-party licenses](#third-party-licenses) below), so there's nothing to
install separately for rendering. If it's ever missing (e.g. running from
source without it on PATH), the app tells you clearly rather than failing
silently.

An NVIDIA GPU + driver is recommended - for the default (CUDA) Whisper
transcription and for NVENC, the fastest video encoder when rendering -
but not strictly required any more: switch `whisper_device`/`verify_device`
to `cpu` in Settings for transcription, and **Render encoder** to
**CPU / Software** on the Video Style tab for rendering. Software encoding
is much slower than NVENC, but it means the app can run on any Windows
machine, GPU or not.

Rendering also auto-detects NVENC at render time and falls back to CPU
encoding on its own if NVENC is requested but doesn't actually work (a
missing GPU, or a driver too old for the NVENC API version ffmpeg wants) -
with a clear one-time notice when that happens, so a fallback never looks
like an unexplained slowdown.

Settings live in `%APPDATA%\OBS AI Highlights\` (`highlight_config.json` +
`.env` for the OBS password) - separate from wherever the app itself is
installed, so it works without admin rights and survives a reinstall.

Game-event OCR also requires the Tesseract OCR application. Install
Tesseract for Windows, then leave its executable on `PATH` or set its
path in Settings → Advanced → Game Events. Configure the OCR region as normalized
left/top/width/height values against the primary display, and enter your
in-game player name for kill/death attribution. If Tesseract is unavailable,
normal transcript/audio capture continues and the error is logged.

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
in Settings → Advanced → Audio & Integrations. Bound to `127.0.0.1` only;
it's never reachable from the network, and it isn't available when
capture isn't running.

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

For code/resource-only releases with no changes to Python, PyInstaller,
or other bundled runtime dependencies, build the optional smaller update
package too:

```bash
python build_app_windows.py --app-only-update
```

This produces both the normal full installer and
`installer-output/OBSAIHighlights-Update-vX.Y.Z.exe`. Attach both to the
GitHub Release to let existing installs take the smaller update while
new installs use the full installer. The app-only package replaces the
executable and app assets, retaining FFmpeg and bundled libraries already
installed. Do not publish that package when runtime dependencies change;
publish only the full installer so the updater automatically falls back
to it.

The first run also downloads and caches a static ffmpeg build (~160MB,
see below) under `vendor/ffmpeg/` - not committed to git, same as
PyInstaller's own build tooling. Produces `dist/OBSAIHighlights/` (the
packaged app) and `installer-output/OBSAIHighlights-Setup-vX.Y.Z.exe`.

## Third-party licenses

The installer bundles an LGPL-only static build of
[ffmpeg](https://ffmpeg.org/) from
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) - GPL-licensed
components (libx264, libx265, ...) are compiled out; NVENC hardware
encoding and libass (subtitle burn-in) are compiled in, since that's
everything this tool's own rendering actually uses. `ffmpeg`'s LGPLv3 text
ships alongside it (`vendor/ffmpeg/FFMPEG-LICENSE.txt` in this repo,
`FFMPEG-LICENSE.txt` next to the installed app). ffmpeg's own source is
available from [ffmpeg.org](https://ffmpeg.org/download.html); this
specific build's source is available from
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds).

## Status

Private, early-stage companion tool - extracted from a church production
system's internal AI-shorts feature and generalized with a preset system.
Packaged as an unsigned Windows installer (no code-signing certificate
yet, so Windows SmartScreen may warn on first run).
