"""Editable controls for a cover preview."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.i18n_helpers import Retranslator

# Discrete focal points for cropping a photo slot (normalised 0..1), in the
# same spirit as CSS ``object-position``. Keeps the crop control to a combo
# box — no extra dialog or drag handling.
_FOCUS_CHOICES: list[tuple[str, tuple[float, float]]] = [
    ("cover_focus_center", (0.5, 0.5)),
    ("cover_focus_top", (0.5, 0.15)),
    ("cover_focus_bottom", (0.5, 0.85)),
    ("cover_focus_left", (0.15, 0.5)),
    ("cover_focus_right", (0.85, 0.5)),
]


class CoverInspector(QWidget):
    changed = pyqtSignal()
    choose_photo = pyqtSignal(str)
    grab_frame = pyqtSignal(str)
    focus_changed = pyqtSignal(str, float, float)
    export_requested = pyqtSignal()
    suggest_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._i18n = Retranslator()
        self._video_available = False
        self._frame_buttons: list = []
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.layout_combo = QComboBox()
        self._i18n.combo_items(self.layout_combo, [
            ("cover_layout_duo", "duo"),
            ("cover_layout_solo", "solo"),
            ("cover_layout_text", "text_only"),
        ])
        self.variant_combo = QComboBox()
        self._i18n.combo_items(self.variant_combo, [
            ("cover_variant_mint", "mint"),
            ("cover_variant_warm", "warm"),
        ])
        self.title_edit = QPlainTextEdit()
        self.title_edit.setMaximumHeight(100)
        self.names_edit = QLineEdit()
        self._i18n.form_row(form, "cover_layout", self.layout_combo)
        self._i18n.form_row(form, "cover_variant", self.variant_combo)
        self._i18n.form_row(form, "cover_title", self.title_edit)
        self._i18n.form_row(form, "cover_names", self.names_edit)
        layout.addLayout(form)
        self.suggest_button = self._i18n.text(QPushButton(), "cover_suggest_title")
        self.suggest_button.clicked.connect(self.suggest_requested.emit)
        layout.addWidget(self.suggest_button)
        for slot in ("photo_a", "photo_b"):
            row = QHBoxLayout()
            button = self._i18n.text(QPushButton(), "cover_choose_photo")
            button.clicked.connect(
                lambda _checked=False, name=slot: self.choose_photo.emit(name)
            )
            row.addWidget(button)
            frame_button = self._i18n.text(QPushButton(), "cover_frame_from_video")
            frame_button.setEnabled(False)
            frame_button.clicked.connect(
                lambda _checked=False, name=slot: self.grab_frame.emit(name)
            )
            self._frame_buttons.append(frame_button)
            row.addWidget(frame_button)
            focus_combo = QComboBox()
            self._i18n.combo_items(focus_combo, _FOCUS_CHOICES)
            focus_combo.currentIndexChanged.connect(
                lambda _idx, name=slot, combo=focus_combo: self._emit_focus(name, combo)
            )
            row.addWidget(focus_combo)
            layout.addLayout(row)
        self.export_button = self._i18n.text(QPushButton(), "cover_export")
        self.export_button.setProperty("variant", "primary")
        self.export_button.clicked.connect(self.export_requested.emit)
        layout.addWidget(self.export_button)
        layout.addStretch()
        self._i18n.bind()
        self.layout_combo.currentIndexChanged.connect(self.changed.emit)
        self.variant_combo.currentIndexChanged.connect(self.changed.emit)
        self.title_edit.textChanged.connect(self.changed.emit)
        self.names_edit.textChanged.connect(self.changed.emit)

    def _emit_focus(self, slot: str, combo: QComboBox) -> None:
        fx, fy = combo.currentData()
        self.focus_changed.emit(slot, fx, fy)

    def set_video_available(self, available: bool) -> None:
        """Enable the per-slot 'frame from video' buttons only when the
        loaded source is a video FFmpeg can pull stills from."""
        self._video_available = available
        for button in self._frame_buttons:
            button.setEnabled(available)

    def state(self) -> tuple[str, str, dict[str, str]]:
        return (
            self.layout_combo.currentData(),
            self.variant_combo.currentData(),
            {"title": self.title_edit.toPlainText(), "names": self.names_edit.text()},
        )
