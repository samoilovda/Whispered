"""
Whispered - Audio Player Widget
QMediaPlayer-based player with position tracking for transcript sync.
Degrades gracefully when Qt Multimedia backend is unavailable.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QSlider,
    QComboBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QUrl

from core.logger import get_logger
from core.i18n import tr
from utils import format_duration

logger = get_logger(__name__)

_MULTIMEDIA_AVAILABLE = False

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    _MULTIMEDIA_AVAILABLE = True
except ImportError:
    logger.warning("PyQt6.QtMultimedia not available; audio player disabled")


def multimedia_available() -> bool:
    return _MULTIMEDIA_AVAILABLE


class PlayerWidget(QWidget):
    """
    Compact audio/video player (audio only shown) with:
      - play/pause, seek ±10 s, position slider, time label
      - playback speed (0.5×–2×)
      - volume slider
      - signal position_changed_sec(float) emitted ~5 times/s
      - method seek_to(seconds)

    If Qt Multimedia backend is unavailable the widget hides itself and all
    public methods become no-ops so the rest of the app is unaffected.
    """

    position_changed_sec = pyqtSignal(float)  # emitted on timer tick
    seek_requested = pyqtSignal(float)         # internal re-use

    def __init__(self, parent=None):
        super().__init__(parent)
        self._available = _MULTIMEDIA_AVAILABLE
        self._duration = 0.0
        self._player: Optional["QMediaPlayer"] = None
        self._audio_output: Optional["QAudioOutput"] = None
        self._setup_ui()
        if not self._available:
            self.setVisible(False)
            return
        self._init_player()
        self._start_ticker()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        # Row 1: controls + time
        controls = QHBoxLayout()
        controls.setSpacing(6)

        self._rewind_btn = QPushButton("⏮ 10s")
        self._rewind_btn.setFixedWidth(52)
        self._rewind_btn.setToolTip(tr("tooltip_rewind"))
        self._rewind_btn.clicked.connect(lambda: self._seek_relative(-10))
        controls.addWidget(self._rewind_btn)

        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedWidth(36)
        self._play_btn.setToolTip(tr("tooltip_play"))
        self._play_btn.clicked.connect(self._toggle_play)
        controls.addWidget(self._play_btn)

        self._forward_btn = QPushButton("10s ⏭")
        self._forward_btn.setFixedWidth(52)
        self._forward_btn.setToolTip(tr("tooltip_forward"))
        self._forward_btn.clicked.connect(lambda: self._seek_relative(10))
        controls.addWidget(self._forward_btn)

        # Position slider
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 1000)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._slider.sliderMoved.connect(self._on_slider_moved)
        self._slider_dragging = False
        controls.addWidget(self._slider, stretch=1)

        # Time label
        self._time_label = QLabel("0:00 / 0:00")
        self._time_label.setProperty("role", "muted")
        self._time_label.setStyleSheet("font-size: 11px;")
        self._time_label.setFixedWidth(90)
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        controls.addWidget(self._time_label)

        layout.addLayout(controls)

        # Row 2: speed + volume
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        speed_label = QLabel(tr("label_speed"))
        speed_label.setProperty("role", "muted")
        speed_label.setStyleSheet("font-size: 11px;")
        row2.addWidget(speed_label)

        self._speed_combo = QComboBox()
        self._speed_combo.setFixedWidth(68)
        for label, val in [("0.5×", 0.5), ("0.75×", 0.75), ("1×", 1.0),
                           ("1.25×", 1.25), ("1.5×", 1.5), ("2×", 2.0)]:
            self._speed_combo.addItem(label, val)
        self._speed_combo.setCurrentIndex(2)  # 1×
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        row2.addWidget(self._speed_combo)

        row2.addSpacing(12)

        vol_label = QLabel(tr("label_volume"))
        vol_label.setProperty("role", "muted")
        vol_label.setStyleSheet("font-size: 11px;")
        row2.addWidget(vol_label)

        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(80)
        self._vol_slider.setFixedWidth(80)
        self._vol_slider.valueChanged.connect(self._on_volume_changed)
        row2.addWidget(self._vol_slider)

        row2.addStretch()
        layout.addLayout(row2)

    def _init_player(self):
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(0.8)

        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio_output)
        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.errorOccurred.connect(self._on_error)

    def _start_ticker(self):
        """Emit position_changed_sec ~5 times/s; also updates slider & label."""
        self._ticker = QTimer(self)
        self._ticker.setInterval(200)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start()

    # ------------------------------------------------------------------ public API

    def load(self, filepath: str):
        """Load a media file. Pass an empty string to unload."""
        if not self._available:
            return
        if not filepath:
            self._player.stop()
            self._player.setSource(QUrl())
            return
        self._player.setSource(QUrl.fromLocalFile(filepath))
        self._play_btn.setText("▶")

    def seek_to(self, seconds: float):
        """Seek the player to a specific position."""
        if not self._available or not self._player:
            return
        ms = max(0, int(seconds * 1000))
        self._player.setPosition(ms)

    def play(self):
        if self._available and self._player:
            self._player.play()

    def pause(self):
        if self._available and self._player:
            self._player.pause()

    def toggle_play(self):
        self._toggle_play()

    def current_position(self) -> float:
        if not self._available or not self._player:
            return 0.0
        return self._player.position() / 1000.0

    # ------------------------------------------------------------------ slots

    def _toggle_play(self):
        if not self._available:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _seek_relative(self, delta_sec: float):
        if not self._available:
            return
        current = self._player.position() / 1000.0
        self.seek_to(current + delta_sec)

    def _on_playback_state(self, state):
        if not self._available:
            return
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_btn.setText("⏸" if playing else "▶")

    def _on_duration_changed(self, ms: int):
        self._duration = ms / 1000.0

    def _on_error(self, error, error_string: str):
        logger.warning("Media player error %s: %s", error, error_string)

    def _on_speed_changed(self, _index: int):
        if not self._available:
            return
        rate = self._speed_combo.currentData()
        self._player.setPlaybackRate(rate)

    def _on_volume_changed(self, value: int):
        if not self._available or not self._audio_output:
            return
        self._audio_output.setVolume(value / 100.0)

    def _on_slider_pressed(self):
        self._slider_dragging = True

    def _on_slider_released(self):
        self._slider_dragging = False
        if self._available and self._duration > 0:
            frac = self._slider.value() / 1000.0
            self.seek_to(frac * self._duration)

    def _on_slider_moved(self, value: int):
        if self._available and self._duration > 0:
            frac = value / 1000.0
            secs = frac * self._duration
            self._time_label.setText(
                f"{_fmt(secs)} / {_fmt(self._duration)}"
            )

    def _tick(self):
        if not self._available or not self._player:
            return
        pos_ms = self._player.position()
        pos_sec = pos_ms / 1000.0
        dur = self._duration

        # Update slider (skip if user is dragging)
        if not self._slider_dragging and dur > 0:
            frac = pos_sec / dur
            self._slider.setValue(int(frac * 1000))

        # Update time label
        self._time_label.setText(f"{_fmt(pos_sec)} / {_fmt(dur)}")

        # Emit signal (throttled by timer interval)
        self.position_changed_sec.emit(pos_sec)


_fmt = format_duration
