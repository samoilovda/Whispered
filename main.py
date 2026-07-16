#!/usr/bin/env python3
"""
Whispered - Main Entry Point
A modern desktop transcription application using whisper.cpp
"""

import sys
import os
import multiprocessing

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _setup_frozen_runtime():
    """Wire up the standalone (PyInstaller) build's external pieces.

    The .app deliberately does NOT bundle pywhispercpp/libwhisper (see
    build.py) — they live next to the whisper models in
    ~/Library/Application Support/Whispered/lib, so the native Metal
    build can be swapped/updated without rebuilding the app. Also, apps
    launched from Finder inherit a minimal PATH that lacks Homebrew, so
    ffmpeg (needed for audio extraction) would not be found without help.
    No-op when running from source: the venv already has everything.
    """
    if not getattr(sys, "frozen", False):
        return
    lib_dir = os.path.expanduser("~/Library/Application Support/Whispered/lib")
    if os.path.isdir(lib_dir):
        sys.path.insert(0, lib_dir)
    path = os.environ.get("PATH", "")
    for extra in ("/opt/homebrew/bin", "/usr/local/bin"):
        if extra not in path.split(os.pathsep):
            path = path + os.pathsep + extra
    os.environ["PATH"] = path


_setup_frozen_runtime()

# In the frozen (PyInstaller) build, transcription child processes are
# spawned by re-executing this same binary. freeze_support() hands those
# invocations to the multiprocessing worker machinery instead of booting
# a second copy of the GUI — without it, clicking Process opened a new
# app window and the transcription never started. It must run after
# _setup_frozen_runtime() (workers import pywhispercpp from the external
# lib dir) but before any Qt import.
if __name__ == "__main__":
    multiprocessing.freeze_support()

# Initialize centralized logging BEFORE any other module imports
from core.logger import setup_logging
setup_logging()

# Log where (and whether) the external whisper stack resolves, so a
# missing lib/ dir in the standalone build is diagnosable from app.log
# instead of surfacing only when the first transcription fails.
import importlib.util as _ilu
import logging as _logging
_spec = _ilu.find_spec("pywhispercpp")
if _spec is not None:
    _logging.getLogger(__name__).info("pywhispercpp resolves from: %s", _spec.origin)
else:
    _logging.getLogger(__name__).warning(
        "pywhispercpp NOT found — transcription will be unavailable. "
        "Standalone builds expect it in ~/Library/Application Support/Whispered/lib"
    )

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QIcon
from ui.main_window import MainWindow
from ui.theme import apply_theme
from config import get_config
from core.i18n import load_locale


def main():
    # Enable high DPI scaling
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    # Load UI locale before any widget is constructed
    cfg = get_config()
    load_locale(cfg.ui_language)

    app = QApplication(sys.argv)
    app.setApplicationName("Whispered")
    app.setApplicationDisplayName("Whispered")

    # Window/taskbar icon. The .app carries its own icon via build.py's
    # --icon, but running from source (and Linux) needs it set here.
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "assets", "icon.png")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # Set premium font stack (prioritizing system native fonts)
    font = QFont()
    font.setFamilies([
        "SF Pro Display",
        "SF Pro Text",
        ".AppleSystemUIFont",
        "Segoe UI",
        "Segoe UI Variable Text",
        "Inter",
        "Roboto",
        "Helvetica Neue",
        "Arial",
        "Sans Serif"
    ])
    font.setPointSize(10)
    app.setFont(font)

    # Apply theme from config (falls back to qdarktheme on error)
    apply_theme(app, cfg.theme)

    # Create and show main window
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
