"""Whispered - Video Panel
Left-hand panel for video mode: fps selector, drop-frame toggle, EDL export.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QFrame, QDoubleSpinBox,
)
from PyQt6.QtCore import Qt, pyqtSignal

from config import get_config, save_config
from ui.i18n_helpers import Retranslator
from core.logger import get_logger

logger = get_logger(__name__)

_FPS_OPTIONS = [24, 25, 30, 60]


class VideoPanel(QWidget):
    """Left-hand panel for video mode settings and EDL export trigger."""

    export_edl_requested = pyqtSignal()
    mark_pauses_requested = pyqtSignal(float)   # emits pause threshold (seconds)
    assemble_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._i18n = Retranslator()
        self._setup_ui()
        self._load_config()
        self._i18n.bind()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        # Section label
        section_label = self._i18n.text(QLabel(), "video_panel_title")
        section_label.setProperty("role", "heading")
        layout.addWidget(section_label)

        # Separator line
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setProperty("role", "divider")
        layout.addWidget(line)

        # FPS row
        fps_row = QHBoxLayout()
        fps_row.setSpacing(8)
        fps_label = self._i18n.text(QLabel(), "video_fps_label")
        fps_label.setProperty("role", "muted")
        fps_row.addWidget(fps_label)

        self._fps_combo = QComboBox()
        self._fps_combo.setFixedWidth(80)
        for fps in _FPS_OPTIONS:
            self._fps_combo.addItem(str(fps), fps)
        self._fps_combo.currentIndexChanged.connect(self._on_fps_changed)
        fps_row.addWidget(self._fps_combo)
        fps_row.addStretch()
        layout.addLayout(fps_row)

        # Drop-frame row
        self._df_checkbox = self._i18n.text(QCheckBox(), "video_drop_frame_label", tooltip="video_drop_frame_tooltip")
        self._df_checkbox.stateChanged.connect(self._on_df_changed)
        layout.addWidget(self._df_checkbox)

        layout.addSpacing(4)

        # Action buttons row
        actions_row = QHBoxLayout()
        actions_row.setSpacing(4)

        # Pause threshold spinbox
        self._pause_spin = QDoubleSpinBox()
        self._pause_spin.setRange(0.1, 5.0)
        self._pause_spin.setSingleStep(0.1)
        self._pause_spin.setValue(0.5)
        self._pause_spin.setSuffix(" s")
        self._pause_spin.setFixedWidth(64)
        self._i18n.text(self._pause_spin, "video_pause_threshold_tooltip", "setToolTip")
        actions_row.addWidget(self._pause_spin)

        self._export_btn = QPushButton("EDL")
        self._export_btn.setProperty("role", "quick-chip")
        self._i18n.text(self._export_btn, "video_export_edl", "setToolTip")
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self.export_edl_requested.emit)
        actions_row.addWidget(self._export_btn)

        self._mark_btn = QPushButton("Mark")
        self._mark_btn.setProperty("role", "quick-chip")
        self._i18n.text(self._mark_btn, "video_mark_pauses", "setToolTip")
        self._mark_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mark_btn.setEnabled(False)
        self._mark_btn.clicked.connect(
            lambda: self.mark_pauses_requested.emit(self._pause_spin.value())
        )
        actions_row.addWidget(self._mark_btn)

        self._assemble_btn = QPushButton("MP4")
        self._assemble_btn.setProperty("role", "quick-chip")
        self._i18n.text(self._assemble_btn, "video_assemble_draft", "setToolTip")
        self._assemble_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._assemble_btn.setEnabled(False)
        self._assemble_btn.clicked.connect(self.assemble_requested.emit)
        actions_row.addWidget(self._assemble_btn)
        actions_row.addStretch()

        layout.addLayout(actions_row)
        layout.addStretch()

    # ------------------------------------------------------------------ public API

    def set_has_transcript(self, has: bool) -> None:
        """Enable or disable transcript-dependent buttons."""
        self._export_btn.setEnabled(has)
        self._mark_btn.setEnabled(has)
        self._assemble_btn.setEnabled(has)

    # ------------------------------------------------------------------ internals

    def _load_config(self):
        cfg = get_config()
        fps = cfg.video_fps
        idx = next((i for i, v in enumerate(_FPS_OPTIONS) if v == fps), 2)  # default 30
        self._fps_combo.blockSignals(True)
        self._fps_combo.setCurrentIndex(idx)
        self._fps_combo.blockSignals(False)

        self._df_checkbox.blockSignals(True)
        self._df_checkbox.setChecked(cfg.video_drop_frame)
        self._df_checkbox.blockSignals(False)

        self._update_df_enabled()

    def _update_df_enabled(self):
        fps = self._fps_combo.currentData()
        can_df = fps in (30, 60)
        self._df_checkbox.setEnabled(can_df)
        if not can_df:
            self._df_checkbox.setChecked(False)

    def _on_fps_changed(self, _index: int):
        self._update_df_enabled()
        cfg = get_config()
        cfg.video_fps = self._fps_combo.currentData()
        cfg.video_drop_frame = self._df_checkbox.isChecked()
        save_config()

    def _on_df_changed(self, _state: int):
        cfg = get_config()
        cfg.video_drop_frame = self._df_checkbox.isChecked()
        save_config()
