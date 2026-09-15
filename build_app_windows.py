# -*- coding: utf-8 -*-
"""Builds the OBS AI Highlights Windows app as a single PyInstaller
onedir bundle, then (if Inno Setup 6 is installed) compiles the
installer around it.

Mirrors the PyInstaller invocation shape used by SermonAI's own
core/build_sss_windows.py (same machine, same Python, already proven to
work there) - scaled down to one target and with no code-signing step,
since this is an unsigned free tool with no signing certificate.

    python build_app_windows.py
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from version import APP_VERSION

SOURCE_ROOT = Path(__file__).resolve().parent

DIST_ROOT = SOURCE_ROOT / "dist"
BUILD_ROOT = SOURCE_ROOT / "build"

APP_NAME = "OBSAIHighlights"
VERSION = APP_VERSION

# A pinned (not the rolling "latest") release from BtbN/FFmpeg-Builds -
# an LGPL-only static build (no GPL code: x264/x265 are compiled out -
# see that repo's variants/win64-lgpl.sh) that still includes NVENC
# (scripts.d/50-ffnvcodec.sh has no LGPL exclusion) and libass (LGPL
# itself) for the subtitle burn-in, which is everything render_clips.py
# actually needs. Verified directly against render_styles.py's real
# filter_complex before being wired in here.
FFMPEG_RELEASE_TAG = "autobuild-2026-09-15-13-18"
FFMPEG_ASSET_NAME = "ffmpeg-n9.0.1-30-g9258bacca5-win64-lgpl-9.0.zip"
FFMPEG_DOWNLOAD_URL = (
    f"https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    f"{FFMPEG_RELEASE_TAG}/{FFMPEG_ASSET_NAME}"
)

# Cached locally, never committed to git - the extracted exe is ~130MB,
# over GitHub's 100MB push limit, the same reason .build-tools/
# (PyInstaller itself) isn't committed either.
VENDOR_FFMPEG_DIR = SOURCE_ROOT / "vendor" / "ffmpeg"
BUNDLED_FFMPEG_EXE = VENDOR_FFMPEG_DIR / "ffmpeg.exe"


def _ensure_bundled_ffmpeg():
    """Downloads and caches the pinned ffmpeg build (see above) so the
    installed app never needs ffmpeg on PATH. A no-op after the first
    build - render_clips.py's own ffmpeg check still falls back to PATH
    if this vendor copy is ever missing (e.g. running from source)."""
    if BUNDLED_FFMPEG_EXE.exists():
        print(f"Using cached ffmpeg: {BUNDLED_FFMPEG_EXE}")
        return

    print()
    print(f"Downloading ffmpeg (one-time, ~160MB): {FFMPEG_ASSET_NAME}")

    VENDOR_FFMPEG_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        zip_path = Path(temp_dir) / FFMPEG_ASSET_NAME

        urllib.request.urlretrieve(FFMPEG_DOWNLOAD_URL, zip_path)

        with zipfile.ZipFile(zip_path) as archive:
            exe_member = next(
                name for name in archive.namelist() if name.endswith("/bin/ffmpeg.exe")
            )
            license_member = next(
                name for name in archive.namelist() if name.endswith("/LICENSE.txt")
            )

            with archive.open(exe_member) as source, open(BUNDLED_FFMPEG_EXE, "wb") as dest:
                shutil.copyfileobj(source, dest)

            with archive.open(license_member) as source, open(
                VENDOR_FFMPEG_DIR / "FFMPEG-LICENSE.txt", "wb"
            ) as dest:
                shutil.copyfileobj(source, dest)

    print(f"ffmpeg cached at: {BUNDLED_FFMPEG_EXE}")


def _run(command):
    print()
    print(">", " ".join(str(part) for part in command))

    subprocess.check_call(command, cwd=SOURCE_ROOT)


def _pyinstaller_command():
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--noupx",
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(BUILD_ROOT),
        "--specpath",
        str(BUILD_ROOT),
        "--name",
        APP_NAME,
        "--hidden-import",
        "obsws_python",
        "--collect-submodules",
        "obsws_python",
    ]

    # sv_ttk ships its .tcl theme scripts and sprite sheets as package
    # data, not Python modules - PyInstaller's import analysis can't see
    # them, so they must be collected explicitly or the packaged app
    # silently falls back to the plain (pre-theme) look. Same reasoning
    # as SermonAI's own build_sss_windows.py.
    if importlib.util.find_spec("sv_ttk") is not None:
        command.extend(["--collect-data", "sv_ttk"])

    # faster-whisper/ctranslate2 ship compiled native CUDA/CPU libraries
    # as package data, not pure Python - a commonly tricky case for
    # PyInstaller's default import analysis. --collect-all is the
    # starting point; if the built app still can't find something at
    # runtime, that's the first place to add more.
    for heavy_package in ("faster_whisper", "ctranslate2"):
        if importlib.util.find_spec(heavy_package) is not None:
            command.extend(["--collect-all", heavy_package])

    icon = SOURCE_ROOT / "icon.ico"
    if icon.exists():
        # --icon sets the .exe file's own resource icon (what Explorer/
        # Task Manager show). A running Tk window has a separate icon
        # of its own (root.iconbitmap()) that needs the file bundled as
        # actual data too, or it falls back to Tk's default icon.
        command.extend(["--icon", str(icon)])
        command.extend(["--add-data", f"{icon};."])

    assets_dir = SOURCE_ROOT / "assets"
    if assets_dir.exists():
        command.extend(["--add-data", f"{assets_dir};assets"])

    if BUNDLED_FFMPEG_EXE.exists():
        command.extend(["--add-data", f"{BUNDLED_FFMPEG_EXE};."])

    ffmpeg_license = VENDOR_FFMPEG_DIR / "FFMPEG-LICENSE.txt"
    if ffmpeg_license.exists():
        command.extend(["--add-data", f"{ffmpeg_license};."])

    command.append(str(SOURCE_ROOT / "app.py"))

    return command


def _verify_outputs():
    exe_path = DIST_ROOT / APP_NAME / f"{APP_NAME}.exe"

    if not exe_path.exists():
        raise RuntimeError(
            "PyInstaller completed but the expected EXE is missing:\n"
            + str(exe_path)
        )

    print()
    print("EXE build verified:")
    print(" -", exe_path)

    return exe_path


def _find_inno_compiler():
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Inno Setup 6"
        / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Inno Setup 6"
        / "ISCC.exe",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


def _build_installer_if_available():
    compiler = _find_inno_compiler()

    if compiler is None:
        print()
        print("Inno Setup 6 was not found.")
        print("The app EXE was built successfully at:")
        print(" -", DIST_ROOT / APP_NAME / f"{APP_NAME}.exe")
        print("Install Inno Setup 6, then run ISCC.exe on installer\\OBSAIHighlights.iss.")
        return None

    _run([str(compiler), str(SOURCE_ROOT / "installer" / "OBSAIHighlights.iss")])

    installer = (
        SOURCE_ROOT
        / "installer-output"
        / f"OBSAIHighlights-Setup-v{VERSION}.exe"
    )

    if not installer.exists():
        raise RuntimeError(
            "Inno Setup completed but the expected installer is missing:\n"
            + str(installer)
        )

    print()
    print("Installer built:")
    print(" -", installer)

    return installer


def main():
    if os.name != "nt":
        print("This build only runs on Windows.")
        raise SystemExit(1)

    print()
    print("=" * 70)
    print(f"OBS AI HIGHLIGHTS - WINDOWS BUILD v{VERSION}")
    print("=" * 70)

    _ensure_bundled_ffmpeg()
    _run(_pyinstaller_command())
    _verify_outputs()
    _build_installer_if_available()

    print()
    print("=" * 70)
    print("BUILD COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
