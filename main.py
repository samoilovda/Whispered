#!/usr/bin/env python3
"""
Whispered - Main Entry Point
A modern desktop transcription application using whisper.cpp
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Initialize centralized logging BEFORE any other module imports
from core.logger import setup_logging
setup_logging()

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
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

    # Set modern font
    font = QFont("Inter", 10)
    if not font.exactMatch():
        font = QFont("Roboto", 10)
    if not font.exactMatch():
        font = QFont("Sans Serif", 10)
    app.setFont(font)

    # Apply theme from config (falls back to qdarktheme on error)
    apply_theme(app, cfg.theme)

    # Create and show main window
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
