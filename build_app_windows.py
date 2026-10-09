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

import argparse
import hashlib
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

# A content-pinned Windows release from BtbN/FFmpeg-Builds. The upstream
# "latest" tag moves, so verify the archive digest before extracting it.
# This LGPL-only static build (no GPL code: x264/x265 are compiled out)
# still includes NVENC and libass for the subtitle burn-in.
FFMPEG_RELEASE_TAG = "latest"
FFMPEG_ASSET_NAME = "ffmpeg-n9.0-latest-win64-lgpl-9.0.zip"
FFMPEG_SHA256 = "8100c1a2b25e19a5c55ed94f1b00780bb650050afdbfce442b2045eb79c12c9d"
FFMPEG_DOWNLOAD_URL = (
    f"https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    f"{FFMPEG_RELEASE_TAG}/{FFMPEG_ASSET_NAME}"
)

# Cached locally, never committed to git - the extracted exe is ~130MB,
# over GitHub's 100MB push limit, the same reason .build-tools/
# (PyInstaller itself) isn't committed either.
VENDOR_FFMPEG_DIR = SOURCE_ROOT / "vendor" / "ffmpeg"
BUNDLED_FFMPEG_EXE = VENDOR_FFMPEG_DIR / "ffmpeg.exe"

TESSERACT_VERSION = "5.4.0.20240606"
TESSERACT_ASSET_NAME = f"tesseract-ocr-w64-setup-{TESSERACT_VERSION}.exe"
TESSERACT_SHA256 = "C885FFF6998E0608BA4BB8AB51436E1C6775C2BAFC2559A19B423E18678B60C9"
TESSERACT_DOWNLOAD_URL = (
    "https://github.com/UB-Mannheim/tesseract/releases/download/"
    f"v{TESSERACT_VERSION}/{TESSERACT_ASSET_NAME}"
)
VENDOR_TESSERACT_DIR = SOURCE_ROOT / "vendor" / "tesseract"
BUNDLED_TESSERACT_INSTALLER = VENDOR_TESSERACT_DIR / TESSERACT_ASSET_NAME


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
        hasher = hashlib.sha256()
        with open(zip_path, "rb") as downloaded:
            for chunk in iter(lambda: downloaded.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        if digest != FFMPEG_SHA256:
            raise RuntimeError(
                f"Downloaded ffmpeg archive SHA-256 mismatch: expected "
                f"{FFMPEG_SHA256}, got {digest}"
            )

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


def _ensure_bundled_tesseract():
    """Download and verify the official Windows Tesseract installer."""
    if BUNDLED_TESSERACT_INSTALLER.exists():
        digest = hashlib.sha256(BUNDLED_TESSERACT_INSTALLER.read_bytes()).hexdigest()
        if digest == TESSERACT_SHA256.lower():
            print(f"Using verified Tesseract installer: {BUNDLED_TESSERACT_INSTALLER}")
            return
        raise RuntimeError(
            "Cached Tesseract installer SHA-256 mismatch: expected "
            f"{TESSERACT_SHA256}, got {digest}"
        )

    print()
    print(f"Downloading Tesseract OCR ({TESSERACT_ASSET_NAME})")
    VENDOR_TESSERACT_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        installer_path = Path(temp_dir) / TESSERACT_ASSET_NAME
        urllib.request.urlretrieve(TESSERACT_DOWNLOAD_URL, installer_path)
        digest = hashlib.sha256(installer_path.read_bytes()).hexdigest()
        if digest != TESSERACT_SHA256.lower():
            raise RuntimeError(
                "Downloaded Tesseract installer SHA-256 mismatch: expected "
                f"{TESSERACT_SHA256}, got {digest}"
            )
        shutil.copy2(installer_path, BUNDLED_TESSERACT_INSTALLER)

    print(f"Verified Tesseract installer cached at: {BUNDLED_TESSERACT_INSTALLER}")


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

    profiles_dir = SOURCE_ROOT / "profiles"
    if profiles_dir.exists():
        command.extend(["--add-data", f"{profiles_dir};profiles"])

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
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
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


def _build_installer_if_available(app_only_update=False):
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

    if app_only_update:
        update_installer = (
            SOURCE_ROOT
            / "installer-output"
            / f"OBSAIHighlights-Update-v{VERSION}.exe"
        )
        _run([
            str(compiler),
            "/DAppOnlyUpdate=1",
            str(SOURCE_ROOT / "installer" / "OBSAIHighlights.iss"),
        ])

        if not update_installer.exists():
            raise RuntimeError(
                "Inno Setup completed but the expected app-only update is missing:\n"
                + str(update_installer)
            )

        print()
        print("App-only update built (does not include the Tesseract installer):")
        print(" -", update_installer)

    return installer


def main():
    parser = argparse.ArgumentParser(description="Build the OBS AI Highlights Windows app.")
    parser.add_argument(
        "--app-only-update",
        action="store_true",
        help=(
            "Also build a smaller update package that replaces app files without "
            "bundling or installing Tesseract OCR."
        ),
    )
    args = parser.parse_args()

    if os.name != "nt":
        print("This build only runs on Windows.")
        raise SystemExit(1)

    print()
    print("=" * 70)
    print(f"OBS AI HIGHLIGHTS - WINDOWS BUILD v{VERSION}")
    print("=" * 70)

    _ensure_bundled_ffmpeg()
    _ensure_bundled_tesseract()
    _run(_pyinstaller_command())
    _verify_outputs()
    _build_installer_if_available(app_only_update=args.app_only_update)

    print()
    print("=" * 70)
    print("BUILD COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
