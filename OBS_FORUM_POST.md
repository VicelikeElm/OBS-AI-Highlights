# OBS AI Highlights - automatic AI-powered clip detection during your stream (free, open source)

**GitHub:** https://github.com/VicelikeElm/OBS-AI-Highlights
**Latest release:** https://github.com/VicelikeElm/OBS-AI-Highlights/releases/latest
**Platform:** Windows 10/11 (NVIDIA GPU recommended, not required)
**License:** free / open source

---

## What it does

OBS AI Highlights watches your stream live, listens to what's being said with a local Whisper speech-to-text model, and automatically saves a clip whenever it hears something worth keeping - a clutch play, a big reaction, a strong point in a talk, whatever counts as a "highlight" for your content. No cloud AI, no account, no API keys - everything runs locally against OBS's own replay buffer over the WebSocket API.

Once a moment is detected, it gets re-verified for accuracy, and can be rendered straight into a finished, captioned, vertical short ready to post - or just trimmed and left at its original resolution if you'd rather finish it yourself in another editor.

I originally built the scoring/detection engine for a completely different use case (a church's sermon highlight pipeline), then generalized it into a standalone tool anyone doing OBS content could use. It's been through a lot of real testing and bug-fixing to get to a first stable 1.0 release.

## Key features

- **Built-in presets** for Gaming, Twitch/Streaming, and Church/Sermon content, or build and name your own fully custom preset (your own trigger phrases, application phrases, reference phrases, and things to actively ignore)
- **Scene-aware detection** - pause clipping on a "BRB" scene, ignore a "Starting Soon" scene entirely, or auto-switch presets when your scene changes (e.g. a "Gameplay" scene switches to your Gaming preset automatically)
- **Audio excitement signal** - a loudness-spike detector catches reactions and hype moments even when Whisper doesn't transcribe an obvious trigger phrase
- **Full clip review workflow** - every detected clip shows up in a Clips tab with its score, detection reasons, transcript, and a preview thumbnail; approve, reject, render, or delete clips individually or in bulk, and manually override the trim points if the auto-detected cut isn't quite right
- **Flexible rendering** - pick a vertical layout (blurred background, full crop, or letterboxed) or turn layout off entirely for the original resolution; captions can be burned in or left off (the .srt is always saved separately either way); renders with NVENC if your GPU supports it, falls back to CPU automatically if it doesn't
- **Ready-to-post metadata** - every rendered clip gets a suggested title, description, hashtags, and its strongest transcript quote, generated deterministically (no cloud AI calls)
- **Session stats** - every capture session tracks how many moments were analyzed, how many got saved vs. rejected, and which phrases were actually responsible for good (or bad) clips, so you can tune your preset against real data
- **Local HTTP API** for Stream Deck / Bitfocus Companion / AutoHotkey - trigger a manual highlight, check status, pause/resume, or switch presets from a hardware button
- **Light and dark themes**, and an installer that bundles ffmpeg - nothing else to install separately
- **Self-updating** - checks for updates on launch, installs silently, and restarts itself, no wizard to click through

## How it works

1. **Capture** - connects to OBS over the WebSocket API, transcribes live audio with a small/fast Whisper model, and scores each moment against your active preset. High-scoring moments trigger an OBS replay-buffer save.
2. **Verify** - re-transcribes each saved clip with a larger, more accurate Whisper model and cross-checks it against the live transcript, sorting clips into Verified or Review.
3. **Render** - takes verified clips and renders them into a finished short, or just trims them at original resolution if you've turned the layout/captions off.

Each stage can run on its own or all automatically as clips come in.

## Requirements

- Windows 10/11
- OBS with the WebSocket server enabled (built into modern OBS)
- An NVIDIA GPU is recommended for the fastest transcription/rendering, but not required - both fall back to CPU automatically

## Install

Download the installer from the [latest release](https://github.com/VicelikeElm/OBS-AI-Highlights/releases/latest) and run it. Settings live in `%APPDATA%\OBS AI Highlights\`, separate from wherever the app itself installs.

---

Happy to answer questions, and bug reports/feature requests are welcome on the [GitHub issues page](https://github.com/VicelikeElm/OBS-AI-Highlights/issues).
