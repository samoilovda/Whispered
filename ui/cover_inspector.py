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

from core.i18n import tr


class CoverInspector(QWidget):
    changed = pyqtSignal()
    choose_photo = pyqtSignal(str)
    export_requested = pyqtSignal()
    suggest_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.layout_combo = QComboBox()
        self.layout_combo.addItem(tr("cover_layout_duo"), "duo")
        self.layout_combo.addItem(tr("cover_layout_solo"), "solo")
        self.layout_combo.addItem(tr("cover_layout_text"), "text_only")
        self.variant_combo = QComboBox()
        self.variant_combo.addItem(tr("cover_variant_mint"), "mint")
        self.variant_combo.addItem(tr("cover_variant_warm"), "warm")
        self.title_edit = QPlainTextEdit()
        self.title_edit.setMaximumHeight(100)
        self.names_edit = QLineEdit()
        form.addRow(tr("cover_layout"), self.layout_combo)
        form.addRow(tr("cover_variant"), self.variant_combo)
        form.addRow(tr("cover_title"), self.title_edit)
        form.addRow(tr("cover_names"), self.names_edit)
        layout.addLayout(form)
        self.suggest_button = QPushButton(tr("cover_suggest_title"))
        self.suggest_button.clicked.connect(self.suggest_requested.emit)
        layout.addWidget(self.suggest_button)
        for slot in ("photo_a", "photo_b"):
            row = QHBoxLayout()
            button = QPushButton(tr("cover_choose_photo"))
            button.clicked.connect(
                lambda _checked=False, name=slot: self.choose_photo.emit(name)
            )
            row.addWidget(button)
            layout.addLayout(row)
        self.export_button = QPushButton(tr("cover_export"))
        self.export_button.setProperty("variant", "primary")
        self.export_button.clicked.connect(self.export_requested.emit)
        layout.addWidget(self.export_button)
        layout.addStretch()
        self.layout_combo.currentIndexChanged.connect(self.changed.emit)
        self.variant_combo.currentIndexChanged.connect(self.changed.emit)
        self.title_edit.textChanged.connect(self.changed.emit)
        self.names_edit.textChanged.connect(self.changed.emit)

    def state(self) -> tuple[str, str, dict[str, str]]:
        return (
            self.layout_combo.currentData(),
            self.variant_combo.currentData(),
            {"title": self.title_edit.toPlainText(), "names": self.names_edit.text()},
        )
