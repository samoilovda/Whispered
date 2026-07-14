"""Whispered - Transcribe Options Popover
Model / language / translate / performance / diarization controls, moved
out of the always-visible header into a popover reachable from the
LaunchBar's gear button (see ui/launch_bar.py).
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
)
from PyQt6.QtCore import Qt, pyqtSignal

from config import get_config
from core.i18n import tr
from utils import WHISPER_MODELS, WHISPER_LANGUAGES, PERFORMANCE_MODES


def _fill_combo(combo: QComboBox, items: list) -> None:
    for item in items:
        if isinstance(item, tuple):
            combo.addItem(item[1], item[0])
        else:
            combo.addItem(item)


class TranscribeOptionsPopover(QFrame):
    """Floating panel with the transcription settings.

    Shown/hidden by LaunchBar; owns the actual model/language/translate/
    performance/diarization widgets so MainWindow can keep referring to
    them under their historical attribute names (self.model_combo, etc.)
    by aliasing to this popover's widgets after construction.
    """

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setProperty("role", "card")
        self._setup_ui()
        self._load_config()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Model
        model_row = QHBoxLayout()
        model_label = QLabel(tr("label_model"))
        model_label.setProperty("role", "muted")
        model_row.addWidget(model_label)
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(200)
        _fill_combo(self.model_combo, WHISPER_MODELS)
        self.model_combo.currentIndexChanged.connect(self.changed.emit)
        model_row.addWidget(self.model_combo, stretch=1)
        layout.addLayout(model_row)

        # Language + translate
        lang_row = QHBoxLayout()
        lang_label = QLabel(tr("label_language"))
        lang_label.setProperty("role", "muted")
        lang_row.addWidget(lang_label)
        self.language_combo = QComboBox()
        self.language_combo.setMinimumWidth(140)
        _fill_combo(self.language_combo, WHISPER_LANGUAGES)
        self.language_combo.currentIndexChanged.connect(self.changed.emit)
        lang_row.addWidget(self.language_combo, stretch=1)
        layout.addLayout(lang_row)

        self.translate_checkbox = QCheckBox("→ EN")
        self.translate_checkbox.setToolTip(tr("tooltip_translate"))
        self.translate_checkbox.toggled.connect(self.changed.emit)
        layout.addWidget(self.translate_checkbox)

        # Performance
        perf_row = QHBoxLayout()
        perf_label = QLabel(tr("label_mode"))
        perf_label.setProperty("role", "muted")
        perf_row.addWidget(perf_label)
        self.perf_combo = QComboBox()
        self.perf_combo.setMinimumWidth(160)
        _fill_combo(self.perf_combo, [(m[0], m[1]) for m in PERFORMANCE_MODES])
        self.perf_combo.setToolTip(tr("tooltip_performance_mode"))
        self.perf_combo.currentIndexChanged.connect(self.changed.emit)
        perf_row.addWidget(self.perf_combo, stretch=1)
        layout.addLayout(perf_row)

        # Diarization
        self.diarization_checkbox = QCheckBox(tr("label_speakers"))
        self.diarization_checkbox.setToolTip(tr("tooltip_diarization"))
        self.diarization_checkbox.toggled.connect(self.changed.emit)
        layout.addWidget(self.diarization_checkbox)

    def _load_config(self) -> None:
        cfg = get_config()
        model_idx = next(
            (i for i, (k, _) in enumerate(WHISPER_MODELS) if k == cfg.default_model), 6
        )
        self.model_combo.setCurrentIndex(model_idx)
        lang_idx = next(
            (i for i, (k, _) in enumerate(WHISPER_LANGUAGES) if k == cfg.default_language), 0
        )
        self.language_combo.setCurrentIndex(lang_idx)
        perf_idx = next(
            (i for i, (k, *_) in enumerate(PERFORMANCE_MODES) if k == cfg.performance_mode), 1
        )
        self.perf_combo.setCurrentIndex(perf_idx)
        self.diarization_checkbox.setChecked(cfg.diarization_enabled)

    def apply_config_defaults(self) -> None:
        """Re-seed from config after Settings dialog changes (Cancel-safe:
        callers only invoke this after a saved change, same as before)."""
        self._load_config()

    def summary_text(self) -> str:
        """Compact one-line summary shown next to the gear button."""
        parts = [self.model_combo.currentText(), self.language_combo.currentText()]
        if self.translate_checkbox.isChecked():
            parts.append("→EN")
        parts.append(self.perf_combo.currentText())
        if self.diarization_checkbox.isChecked():
            parts.append(tr("label_speakers"))
        return " · ".join(parts)

    def show_below(self, anchor: QWidget) -> None:
        """Position and show the popover just under the given widget."""
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        self.move(pos.x(), pos.y() + 4)
        self.show()
