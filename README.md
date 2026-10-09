# OBS AI Highlights

> **Alpha testing:** This app is still in alpha. Expect bugs, incomplete features, and changes as it is tested and improved.

[![Latest release](https://img.shields.io/github/v/release/VicelikeElm/OBS-AI-Highlights?label=latest%20release)](https://github.com/VicelikeElm/OBS-AI-Highlights/releases/latest)

**[Download the latest Windows installer](https://github.com/VicelikeElm/OBS-AI-Highlights/releases/latest)** - grab the `.exe` from the Assets section of the latest release.

Built by [Vice Media Solutions](assets/vivce_media_solutions.png).

## What's new in v1.1.2

- Prevent the updater from reinstalling Tesseract when a valid installation
  already exists in the standard Program Files location.

## What's new in v1.1.1

- Keep **Save Settings** visible in a fixed footer while scrolling through
  Settings, so it doesn't get hidden below long settings tabs.

- Add Minecraft Java, Minecraft Bedrock, and Destiny 2 profiles alongside
  configurable per-game profiles and event controls.
- Track game events and manual markers against full OBS recordings, with an
  option to add native OBS chapters as events happen (OBS 30.2+ and Hybrid MP4).
- Export recording timelines as Text, Final Cut Pro XML, Premiere Pro XML,
  DaVinci Resolve EDL, or CSV.
- Filter clips by game or event tag, and review event counts, rounds,
  achievements, and saved clips in game-session stats.

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

- **Run** — choose lightweight Game events or the existing AI audio/transcript
  capture. Game events mode avoids loading Whisper or capturing audio; it
  watches the selected game's on-screen feed and saves/tag OBS Replay Buffer
  clips when selected events are recognized. It is designed for low resource
  use, but screen OCR can miss or misread text; Tesseract is bundled with
  the installer.
  Rainbow Six Siege has event patterns. Tarkov process detection is included,
  but its OCR event patterns still need in-game verification before automatic
  Tarkov event clips can be relied on. Minecraft Java, Minecraft Bedrock,
  and Destiny 2 profiles are included with conservative death-feed patterns;
  validate their OCR region and visible event text in-game. Minecraft Java
  detection checks the Java command line so unrelated Java applications are
  not selected automatically.
  A quick-start checklist explains OBS Replay Buffer and the game/name
  settings. Plain-language status messages explain what the app is doing
  and where to go next.
  Automatic clip preparation and rendering can be enabled here; manual
  actions remain available. A short, friendly activity feed shows
  important progress and warnings, while full technical details stay
  hidden until requested. Use **Check setup** to expand a checklist for the
  OBS connection, the needed audio/OCR setup for the selected mode, and
  recording/output folders before capture.
  The check is advisory for OBS readiness, which doesn't prevent capture
  from waiting for OBS to become active. Game Highlights checks that
  Tesseract is available and explains how to configure another installation.
  New installations get a short setup guide, and a
  **Review clips** shortcut appears when clips are available. Empty Clips
  and Sessions views explain what to do next. The Run tab also reports a
  detected supported game and its profile. The game dropdown includes
  Auto-detect, Rainbow Six Siege, Escape from Tarkov, Minecraft (Java),
  Minecraft Bedrock, Destiny 2, and Custom. For a
  custom game, enter its display name and Windows process name; supported
  games can also override their detected process names. Each game's process
  names and in-game player name are saved separately and take effect on the
  next capture. Custom profiles use the common basic HUD
  event recognition; game-specific event patterns may still need tuning.
  In Settings → Advanced → Game Events, tune the OCR region and interval per
  game, choose which recognized events trigger a clip, and set the footage
  window before and after an event. Defaults are 20 seconds before and
  5 seconds after, so set OBS Replay Buffer to at least 25 seconds. The AI
  audio/transcript mode can still use selected game events for extra context.
- **Sessions** — recording-timeline sessions store OCR game events and
  manual markers at their elapsed time in the ordinary OBS recording. Select
  a timeline session to review its markers or export them for a video editor.
  The export dropdown supports Text, Final Cut Pro XML, Premiere Pro XML,
  DaVinci Resolve EDL, and CSV. Optionally enable
  **Add native OBS chapters to full recordings** in Settings → Advanced → Game Events
  to add each marker to the recording
  while it is being made. OBS 30.2+ and the **Hybrid MP4** recording format
  are required for VLC-visible chapters. Other formats still retain the
  separate timeline and CSV export; Replay Buffer clips are not changed.
  The local `/mark` API can add a manual marker from a Stream Deck or
  Companion button.
- **Clips** — every detected clip in one list, across every pipeline
  stage, with its score, status, duration, and detection reasons/full
  transcript. Play a clip in your default video player, Approve or
  Reject a Review clip (non-destructive - nothing is deleted, so a
  reject is easy to undo), Render a Verified one right now instead of
  waiting for the next batch, or Delete a clip you don't want outright.
  Ctrl+click/Shift+click to select several clips and approve, reject,
  render, or delete them all at once. Filter by status, game, or event tag
  and click a column header to sort. Selecting a clip shows a preview thumbnail and
  its trim points - override the start/end time directly if the
  auto-detected trim cut off a word or left in too much dead air, then
  re-render to see it.
- **Sessions** — every capture run is tracked as a session. AI sessions show
  how many moments were analyzed, how many were ignored/possible/saved, the
  average score, and which phrases were actually most responsible for
  good (or rejected) clips, and can include game-event stats too.
  Game-event sessions show event totals, completed rounds, notable
  achievements, and saved game clips. Rename or delete a session's stats record
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

The Windows installer includes Tesseract OCR and English language data, so
Game Highlights does not require a separate OCR download or system-wide
installation. Tesseract is installed once under your Windows user profile
and reused by subsequent app updates. Uninstall Tesseract separately from
Windows Apps if you no longer want the shared OCR runtime. The optional
Tesseract path in Settings → Advanced → Game Events can point to a different
installation; leave it blank to use the app-managed copy. Choose the game and
enter your in-game player name on the Run tab. Configure the OCR region as
normalized left/top/width/height values against the primary display if the
event feed is not recognized. The per-game event checkboxes control which
recognized events trigger clips. Configure OBS Replay Buffer for at least
the selected before + after duration (25 seconds by default); the app waits
for the after-event window before saving. AI audio/transcript mode also uses
game OCR for optional event context when enabled.

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

While either capture mode is running, a small local HTTP API is available
for external tools to trigger and query it - configurable (enable/port)
in Settings → Advanced → Audio & Integrations. Bound to `127.0.0.1` only;
it's never reachable from the network, and it isn't available when
capture isn't running.

```
GET  /status              -> { "capturing": true, "preset": "Gaming", "paused": false, "clips_saved": 3 }
POST /highlight            -> save whatever's in the replay buffer right now, bypassing the phrase-scoring gate
POST /mark                 -> mark the current time in an active OBS recording (optional JSON {"label":"Round win"})
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
curl -X POST -H "Content-Type: application/json" -d "{\"label\":\"Round win\"}" http://127.0.0.1:8756/mark
curl -X POST http://127.0.0.1:8756/preset/gaming
```

`/mark` is consumed by the capture loop and only writes a marker while OBS
is actively recording. OCR-detected game events are added to the same
recording timeline automatically. With native OBS chapters enabled, both
types are also sent to OBS at that moment. `/highlight` saves a Replay Buffer
clip; timeline markers and chapters are for full recordings only.

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
new installs use the full installer. The update package replaces the
executable and app assets and installs the bundled Tesseract OCR runtime
when it is not already present; it retains FFmpeg and the other bundled
libraries. Changes to PyInstaller-bundled Python/native libraries still
require the full installer.

The build downloads and caches a static ffmpeg build (~160MB, see below)
under `vendor/ffmpeg/` and the pinned Tesseract Windows installer under
`vendor/tesseract/`. These build artifacts are not committed to git.
Produces `dist/OBSAIHighlights/` (the packaged app) and
`installer-output/OBSAIHighlights-Setup-vX.Y.Z.exe`.

## Third-party licenses

The installer includes Tesseract OCR from
[UB Mannheim's Windows distribution](https://github.com/UB-Mannheim/tesseract),
installed privately in the app folder from its upstream Windows installer.
Tesseract is licensed under [Apache 2.0](https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE).
The installer also bundles an LGPL-only static build of
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
