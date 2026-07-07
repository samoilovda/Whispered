"""
Whispered – Recorder Widget
Compact mic-record button + level meter + timer for the header bar.
"""

from __future__ import annotations

import time
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLabel, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from core.logger import get_logger
from core.i18n import tr
from utils import format_duration

logger = get_logger(__name__)


class RecorderWidget(QWidget):
    """Compact recorder UI embedded in the header bar.

    Signals
    -------
    file_ready(str)    — path to finished WAV, ready for transcription
    error(str)         — human-readable error message
    """

    file_ready = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recorder = None      # lazily instantiated
        self._start_ts: float = 0.0
        self._device: Optional[int] = None
        self._setup_ui()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Record button
        self._record_btn = QPushButton("⏺")
        self._record_btn.setToolTip(tr("tooltip_record"))
        self._record_btn.setFixedSize(32, 32)
        self._record_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._record_btn.clicked.connect(self._toggle_recording)
        layout.addWidget(self._record_btn)

        # Pause button (visible only while recording)
        self._pause_btn = QPushButton("⏸")
        self._pause_btn.setToolTip(tr("tooltip_record_pause"))
        self._pause_btn.setFixedSize(28, 28)
        self._pause_btn.setVisible(False)
        self._pause_btn.setCheckable(True)
        self._pause_btn.toggled.connect(self._on_pause_toggled)
        layout.addWidget(self._pause_btn)

        # Timer label
        self._timer_label = QLabel("00:00")
        self._timer_label.setStyleSheet("color: #888; font-size: 11px; min-width: 36px;")
        self._timer_label.setVisible(False)
        layout.addWidget(self._timer_label)

        # Level meter
        self._level_bar = QProgressBar()
        self._level_bar.setRange(0, 100)
        self._level_bar.setValue(0)
        self._level_bar.setTextVisible(False)
        self._level_bar.setFixedHeight(6)
        self._level_bar.setFixedWidth(60)
        self._level_bar.setVisible(False)
        layout.addWidget(self._level_bar)

        # Timer
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._update_timer)

    # ------------------------------------------------------------------ public

    def set_device(self, device_index: Optional[int]) -> None:
        self._device = device_index

    # ------------------------------------------------------------------ internals

    def _get_recorder(self):
        if self._recorder is None:
            from core.recorder import Recorder
            self._recorder = Recorder(self)
            self._recorder.level_changed.connect(self._on_level)
            self._recorder.error_occurred.connect(self._on_error)
        return self._recorder

    def _toggle_recording(self):
        rec = self._get_recorder()
        if rec.is_recording():
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        rec = self._get_recorder()
        rec.start(device=self._device)
        if not rec.is_recording():
            return  # error was emitted
        self._start_ts = time.monotonic()
        self._record_btn.setText("⏹")
        self._record_btn.setStyleSheet("color: #ef4444;")
        self._pause_btn.setVisible(True)
        self._timer_label.setVisible(True)
        self._level_bar.setVisible(True)
        self._timer.start()

    def _stop_recording(self):
        self._timer.stop()
        rec = self._get_recorder()
        path = rec.stop()
        self._record_btn.setText("⏺")
        self._record_btn.setStyleSheet("")
        self._pause_btn.setChecked(False)
        self._pause_btn.setVisible(False)
        self._timer_label.setVisible(False)
        self._level_bar.setVisible(False)
        self._level_bar.setValue(0)
        if path:
            self.file_ready.emit(path)

    def _on_pause_toggled(self, paused: bool):
        rec = self._get_recorder()
        if paused:
            rec.pause()
            self._record_btn.setStyleSheet("color: #f59e0b;")
        else:
            rec.resume()
            self._record_btn.setStyleSheet("color: #ef4444;")

    def _update_timer(self):
        rec = self._get_recorder()
        if rec.is_recording():
            self._timer_label.setText(format_duration(rec.elapsed_seconds))

    def _on_level(self, rms: float):
        # Map RMS 0–0.3 → meter 0–100 (clamp)
        value = min(100, int(rms / 0.3 * 100))
        self._level_bar.setValue(value)

    def _on_error(self, msg: str):
        self._stop_recording()
        self.error.emit(msg)
