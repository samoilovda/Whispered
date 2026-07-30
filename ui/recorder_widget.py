"""
Whispered – Recorder Widget
Compact mic-record button + level meter + timer for the header bar.
"""

from __future__ import annotations

import time
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QProgressBar,
    QComboBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from core.logger import get_logger
from core.i18n import tr
from ui.theme import set_role
from ui.icons import IconColors, get_icon
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        self.setProperty("role", "form-section")

        device_row = QHBoxLayout()
        device_label = QLabel(tr("settings_mic_device"))
        device_label.setProperty("role", "muted")
        device_row.addWidget(device_label)
        self._device_combo = QComboBox()
        self._device_combo.addItem(tr("settings_mic_default"), None)
        self._device_combo.currentIndexChanged.connect(
            lambda _index: self.set_device(self._device_combo.currentData())
        )
        device_row.addWidget(self._device_combo, stretch=1)
        refresh_btn = QPushButton(tr("recorder_refresh_devices"))
        refresh_btn.clicked.connect(self._refresh_devices)
        device_row.addWidget(refresh_btn)
        layout.addLayout(device_row)

        controls = QHBoxLayout()
        controls.setSpacing(12)

        # Record button
        self._record_btn = QPushButton(tr("recorder_start"))
        self._record_btn.setIcon(get_icon("microphone", IconColors.WHITE, 16))
        self._record_btn.setProperty("variant", "primary")
        self._record_btn.setToolTip(tr("tooltip_record"))
        self._record_btn.setMinimumHeight(40)
        self._record_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._record_btn.clicked.connect(self._toggle_recording)
        controls.addWidget(self._record_btn)

        # Pause button (visible only while recording)
        self._pause_btn = QPushButton(tr("recorder_pause"))
        self._pause_btn.setToolTip(tr("tooltip_record_pause"))
        self._pause_btn.setMinimumHeight(40)
        self._pause_btn.setVisible(False)
        self._pause_btn.setCheckable(True)
        self._pause_btn.toggled.connect(self._on_pause_toggled)
        controls.addWidget(self._pause_btn)
        controls.addStretch()

        # Timer label
        self._timer_label = QLabel("00:00")
        self._timer_label.setProperty("role", "page-title")
        controls.addWidget(self._timer_label)
        layout.addLayout(controls)

        # Level meter
        self._level_bar = QProgressBar()
        self._level_bar.setRange(0, 100)
        self._level_bar.setValue(0)
        self._level_bar.setTextVisible(False)
        self._level_bar.setFixedHeight(6)
        self._level_bar.setMinimumWidth(260)
        layout.addWidget(self._level_bar)

        self._status_label = QLabel(tr("recorder_ready"))
        self._status_label.setProperty("role", "muted")
        layout.addWidget(self._status_label)

        # Timer
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._update_timer)

    # ------------------------------------------------------------------ public

    def set_device(self, device_index: Optional[int]) -> None:
        self._device = device_index
        index = self._device_combo.findData(device_index)
        if index >= 0 and index != self._device_combo.currentIndex():
            self._device_combo.blockSignals(True)
            self._device_combo.setCurrentIndex(index)
            self._device_combo.blockSignals(False)

    def shutdown(self) -> None:
        """Stop an active recording during application shutdown.
        Part of the Shutdownable protocol (ui/shutdownable.py)."""
        if self._recorder is not None and self._recorder.is_recording():
            self._stop_recording()

    def _refresh_devices(self) -> None:
        current = self._device
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        self._device_combo.addItem(tr("settings_mic_default"), None)
        try:
            from core.recorder import list_input_devices

            for device in list_input_devices():
                self._device_combo.addItem(device["name"], device["index"])
        except Exception as exc:
            self._status_label.setText(str(exc))
            set_role(self._status_label, "danger-text")
        index = self._device_combo.findData(current)
        self._device_combo.setCurrentIndex(index if index >= 0 else 0)
        self._device_combo.blockSignals(False)

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
        self._record_btn.setText(tr("recorder_stop"))
        set_role(self._record_btn, "danger-text")
        self._pause_btn.setVisible(True)
        self._status_label.setText(tr("recorder_recording"))
        set_role(self._status_label, "success-text")
        self._timer.start()

    def _stop_recording(self):
        self._timer.stop()
        rec = self._get_recorder()
        path = rec.stop()
        self._record_btn.setText(tr("recorder_start"))
        set_role(self._record_btn, "")
        self._pause_btn.setChecked(False)
        self._pause_btn.setVisible(False)
        self._timer_label.setText("00:00")
        self._level_bar.setValue(0)
        self._status_label.setText(tr("recorder_ready"))
        set_role(self._status_label, "muted")
        if path:
            self.file_ready.emit(path)

    def _on_pause_toggled(self, paused: bool):
        rec = self._get_recorder()
        if paused:
            rec.pause()
            set_role(self._record_btn, "warning-text")
            self._pause_btn.setText(tr("recorder_resume"))
            self._status_label.setText(tr("recorder_paused"))
            set_role(self._status_label, "warning-text")
        else:
            rec.resume()
            set_role(self._record_btn, "danger-text")
            self._pause_btn.setText(tr("recorder_pause"))
            self._status_label.setText(tr("recorder_recording"))
            set_role(self._status_label, "success-text")

    def _update_timer(self):
        rec = self._get_recorder()
        if rec.is_recording():
            self._timer_label.setText(format_duration(rec.elapsed_seconds))

    def _on_level(self, rms: float):
        # Map RMS 0–0.3 → meter 0–100 (clamp)
        value = min(100, int(rms / 0.3 * 100))
        self._level_bar.setValue(value)

    def _on_error(self, msg: str):
        # ``Recorder.start`` can fail before it ever enters the recording
        # state.  Calling the normal stop path in that case used to emit its
        # empty WAV reservation as ``file_ready``.
        if self._recorder is not None and self._recorder.is_recording():
            self._stop_recording()
        else:
            self._timer.stop()
            self._record_btn.setText(tr("recorder_start"))
            set_role(self._record_btn, "")
            self._pause_btn.setChecked(False)
            self._pause_btn.setVisible(False)
            self._timer_label.setText("00:00")
            self._level_bar.setValue(0)
        self._status_label.setText(msg)
        set_role(self._status_label, "danger-text")
        self.error.emit(msg)
