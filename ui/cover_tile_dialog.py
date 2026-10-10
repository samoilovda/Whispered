"""Whispered UI - "whose photo is this?" for a video-call still.

A frame grabbed from a Zoom-style recording shows every participant; a
cover photo slot needs one of them. ``speaker_crops()`` finds each
participant's picture (``covers.tiles.speaker_tiles``) and
``SpeakerTileDialog`` lets the user pick one when there are several.

Opened with ``.exec()`` and rebuilt each time, so (per CLAUDE.md's i18n
notes) it is deliberately not retranslated live.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QImage, QPixmap
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from core.i18n import tr
from covers.tiles import Rect, speaker_tiles

_THUMB_HEIGHT = 180


def image_to_rgb(image: QImage) -> np.ndarray:
    """H×W×3 uint8 copy of *image* for the Qt-free tile detection."""
    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    width, height = rgb.width(), rgb.height()
    pointer = rgb.constBits()
    pointer.setsize(rgb.sizeInBytes())
    # voidptr supports the buffer protocol; PyQt6's stub doesn't say so.
    rows = np.frombuffer(pointer, np.uint8).reshape(  # type: ignore[call-overload]
        height, rgb.bytesPerLine())
    return rows[:, : width * 3].reshape(height, width, 3).copy()


def speaker_crops(image: QImage) -> list[Rect]:
    """Each participant's picture in *image* (one rect for a single view)."""
    if image.isNull():
        return []
    return speaker_tiles(image_to_rgb(image))


def save_crop(image: QImage, rect: Rect, target: Path) -> Path:
    """Write *rect* of *image* to *target* (PNG) and return it."""
    image.copy(rect.x, rect.y, rect.w, rect.h).save(str(target), "PNG")
    return target


class SpeakerTileDialog(QDialog):
    """One button per participant; ``selected`` is the chosen index."""

    def __init__(self, image: QImage, tiles: list[Rect], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("cover_tile_title"))
        self.selected: int | None = None
        layout = QVBoxLayout(self)
        hint = QLabel(tr("cover_tile_hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        for index, rect in enumerate(tiles):
            crop = image.copy(rect.x, rect.y, rect.w, rect.h)
            pixmap = QPixmap.fromImage(crop).scaledToHeight(
                _THUMB_HEIGHT, Qt.TransformationMode.SmoothTransformation)
            button = QPushButton()
            button.setIcon(QIcon(pixmap))
            button.setIconSize(QSize(pixmap.width(), pixmap.height()))
            button.setToolTip(tr("cover_tile_pick", n=index + 1))
            button.clicked.connect(lambda _checked=False, i=index: self._pick(i))
            row.addWidget(button)
        layout.addLayout(row)
        whole = QPushButton(tr("cover_tile_whole"))
        whole.clicked.connect(self.reject)
        layout.addWidget(whole, alignment=Qt.AlignmentFlag.AlignRight)

    def _pick(self, index: int) -> None:
        self.selected = index
        self.accept()
