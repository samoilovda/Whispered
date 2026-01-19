#!/usr/bin/env python3
"""
Whispered - Main Entry Point
A modern desktop transcription application using whisper.cpp
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
import qdarktheme

from ui.main_window import MainWindow


def main():
    # Enable high DPI scaling
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    
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
    
    # Apply dark theme
    qdarktheme.setup_theme(
        theme="dark",
        custom_colors={
            "[dark]": {
                "primary": "#6366f1",  # Indigo accent
                "primary>button.hoverBackground": "#818cf8",
            }
        }
    )
    
    # Create and show main window
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
