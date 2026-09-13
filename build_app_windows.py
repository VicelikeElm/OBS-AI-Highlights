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
import subprocess
import sys
from pathlib import Path

from version import APP_VERSION

SOURCE_ROOT = Path(__file__).resolve().parent

DIST_ROOT = SOURCE_ROOT / "dist"
BUILD_ROOT = SOURCE_ROOT / "build"

APP_NAME = "OBSAIHighlights"
VERSION = APP_VERSION


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
        command.extend(["--icon", str(icon)])

    assets_dir = SOURCE_ROOT / "assets"
    if assets_dir.exists():
        command.extend(["--add-data", f"{assets_dir};assets"])

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

    _run(_pyinstaller_command())
    _verify_outputs()
    _build_installer_if_available()

    print()
    print("=" * 70)
    print("BUILD COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
