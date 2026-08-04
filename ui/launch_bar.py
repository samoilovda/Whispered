"""Whispered - Launch Bar
The Library page's single entry point for "start working": a preset
combo, the Process button, and a gear button that opens the transcribe
options popover. Replaces the old always-visible header row of model/
language/performance/diarization controls.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QComboBox, QPushButton, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics

from config import get_config, save_config
from core.i18n import tr
from ui.icons import get_icon, IconColors
from ui.transcribe_options import TranscribeOptionsPopover

# (preset key, i18n key) — order matters, shown in this order in the combo.
PRESETS = (
    ("transcribe_only", "preset_transcribe_only"),
    ("youtube", "preset_youtube"),
    ("article", "preset_article"),
    ("full", "preset_full"),
)


class LaunchBar(QWidget):
    """Preset combo + Process button + options gear, docked in the header."""

    process_requested = pyqtSignal()
    preset_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._summary_full_text = ""
        self.options = TranscribeOptionsPopover(self)
        self.options.changed.connect(self._update_summary)
        self._setup_ui()
        self._load_config()
        self._update_summary()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        preset_label = QLabel(tr("launch_preset_label"))
        preset_label.setProperty("role", "muted")
        layout.addWidget(preset_label)

        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(220)
        for key, label_key in PRESETS:
            self.preset_combo.addItem(tr(label_key), key)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        layout.addWidget(self.preset_combo)

        self.process_btn = QPushButton(tr("btn_process"))
        self.process_btn.setIcon(get_icon('play', IconColors.WHITE, 14))
        self.process_btn.setEnabled(False)
        self.process_btn.setProperty("variant", "primary")
        self.process_btn.setToolTip(tr("tooltip_process_disabled"))
        self.process_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.process_btn.clicked.connect(self.process_requested.emit)
        layout.addWidget(self.process_btn)

        layout.addSpacing(8)

        self.options_btn = QPushButton()
        self.options_btn.setIcon(get_icon('settings', IconColors.default(), 14))
        self.options_btn.setToolTip(tr("tooltip_transcribe_options"))
        self.options_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.options_btn.clicked.connect(self._toggle_options)
        layout.addWidget(self.options_btn)

        self.summary_label = QLabel("")
        self.summary_label.setProperty("role", "dim")
        self.summary_label.setStyleSheet("font-size: 11px;")
        # Ignored so the layout doesn't size this from its (long, unelided)
        # text — it gets whatever width is left instead, and that width is
        # what resizeEvent() below elides the text against. Without this
        # the label just requested its full sizeHint and got clipped by
        # the window edge mid-word once the line was too long to fit.
        self.summary_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.summary_label, stretch=1)

    def _load_config(self) -> None:
        saved = get_config().launch_preset
        idx = next(
            (i for i in range(self.preset_combo.count())
             if self.preset_combo.itemData(i) == saved),
            0,
        )
        self.preset_combo.setCurrentIndex(idx)

    def _on_preset_changed(self, _index: int) -> None:
        key = self.preset_combo.currentData()
        cfg = get_config()
        cfg.launch_preset = key
        save_config()
        self.preset_changed.emit(key)

    def _toggle_options(self) -> None:
        if self.options.isVisible():
            self.options.hide()
        else:
            self.options.show_below(self.options_btn)

    def _update_summary(self) -> None:
        self._summary_full_text = self.options.summary_text()
        self.summary_label.setToolTip(self._summary_full_text)
        self._elide_summary()

    def _elide_summary(self) -> None:
        metrics = QFontMetrics(self.summary_label.font())
        elided = metrics.elidedText(
            self._summary_full_text, Qt.TextElideMode.ElideRight, self.summary_label.width()
        )
        self.summary_label.setText(elided)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide_summary()

    def current_preset(self) -> str:
        return self.preset_combo.currentData() or "transcribe_only"

    def set_process_enabled(self, enabled: bool) -> None:
        self.process_btn.setEnabled(enabled)
        self.process_btn.setToolTip(
            tr("tooltip_process") if enabled else tr("tooltip_process_disabled")
        )
