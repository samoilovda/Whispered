#!/usr/bin/env python3
"""Render deterministic UI states without touching user application data."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import config
import core.history as history
from core.i18n import load_locale
from ui.theme import apply_theme


SIZES = ((1100, 700), (1440, 900))
SECTIONS = ("library", "queue", "recorder", "live")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def _inside(parent, child) -> bool:
    top_left = child.mapTo(parent, child.rect().topLeft())
    bottom_right = child.mapTo(parent, child.rect().bottomRight())
    return parent.rect().contains(top_left) and parent.rect().contains(bottom_right)


def render(output: Path, check: bool = False) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    config.CONFIG_DIR = output
    config.CONFIG_FILE = output / "config.json"
    history._store = history.HistoryStore(output / "history.sqlite3")
    app = QApplication.instance() or QApplication(sys.argv)
    rendered: list[Path] = []

    for language in ("ru", "en"):
        for theme in ("dark", "light"):
            config._config = config.Config(
                live_transcription_enabled=True,
                ui_language=language,
                theme=theme,
            )
            load_locale(language)
            apply_theme(app, theme)
            from ui.main_window import MainWindow
            from ui.settings_dialog import SettingsDialog

            window = MainWindow()
            window.show()
            app.processEvents()
            for width, height in SIZES:
                window.resize(width, height)
                for key in SECTIONS:
                    window._on_section_changed(key)
                    app.processEvents()
                    path = output / f"{language}-{theme}-{width}x{height}-{key}.png"
                    if not window.grab().save(str(path)):
                        raise RuntimeError(f"Could not render {path}")
                    rendered.append(path)
                    if check:
                        for widget in window.findChildren(type(window.centralWidget())):
                            if widget.isVisible() and widget.minimumWidth() > 0:
                                if widget.width() <= 0 or widget.height() <= 0:
                                    raise AssertionError(f"Invalid geometry: {widget.objectName()}")
                window._stack.setCurrentIndex(window._record_index)
                app.processEvents()
                path = output / f"{language}-{theme}-{width}x{height}-record.png"
                window.grab().save(str(path))
                rendered.append(path)

            settings = SettingsDialog(window)
            settings.resize(840, 680)
            settings.show()
            app.processEvents()
            path = output / f"{language}-{theme}-settings.png"
            settings.grab().save(str(path))
            rendered.append(path)
            if check and not _inside(settings, settings.layout().itemAt(0).widget()):
                raise AssertionError("Settings content is outside its viewport")
            settings.close()
            window.close()
            app.processEvents()
    return rendered


def main() -> int:
    args = _parse_args()
    output = args.output or Path(tempfile.gettempdir()) / "whispered-ui-gallery"
    rendered = render(output, check=args.check)
    print(f"Rendered {len(rendered)} states to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
