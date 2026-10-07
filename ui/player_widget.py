"""
Whispered - Audio Player Widget
QMediaPlayer-based player with position tracking for transcript sync.
Degrades gracefully when Qt Multimedia backend is unavailable.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QSlider,
    QComboBox, QStyle, QStyleOptionSlider, QToolTip
)
from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui import QPainter

from core.logger import get_logger
from ui.i18n_helpers import Retranslator
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


class _ChapterMarks(QWidget):
    """A thin strip under the position slider with a tick per chapter.
    Hovering a tick names the chapter; clicking it seeks there. Lined up
    with the slider's groove by PlayerWidget._sync_marks_geometry()."""

    seek_requested = pyqtSignal(float)

    _HIT_PX = 6          # how close the pointer must be to a tick

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(8)
        self.setMouseTracking(True)
        self._chapters: list[tuple[float, str]] = []
        self._duration = 0.0
        self._inset = 0              # half the slider handle: where 0 sits

    def set_chapters(self, chapters: list[tuple[float, str]], duration: float) -> None:
        self._chapters = chapters
        self._duration = duration
        self.setVisible(bool(chapters) and duration > 0)
        self.update()

    def set_inset(self, inset: int) -> None:
        self._inset = inset
        self.update()

    def tick_x(self, start: float) -> int:
        span = max(1, self.width() - 2 * self._inset)
        frac = min(max(start / self._duration, 0.0), 1.0) if self._duration > 0 else 0.0
        return self._inset + int(round(frac * span))

    def chapter_at(self, x: int) -> Optional[tuple[float, str]]:
        best = None
        best_dist = self._HIT_PX + 1
        for chapter in self._chapters:
            dist = abs(self.tick_x(chapter[0]) - x)
            if dist < best_dist:
                best, best_dist = chapter, dist
        return best

    def paintEvent(self, event):  # noqa: N802 — Qt override
        if not self._chapters or self._duration <= 0:
            return
        painter = QPainter(self)
        color = self.palette().highlight().color()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        for start, _title in self._chapters:
            painter.drawRect(self.tick_x(start) - 1, 0, 2, self.height())
        painter.end()

    def mouseMoveEvent(self, event):  # noqa: N802 — Qt override
        chapter = self.chapter_at(int(event.position().x()))
        if chapter is None:
            self.unsetCursor()
            QToolTip.hideText()
            return
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        QToolTip.showText(
            event.globalPosition().toPoint(), f"{_fmt(chapter[0])}  {chapter[1]}", self,
        )

    def mousePressEvent(self, event):  # noqa: N802 — Qt override
        chapter = self.chapter_at(int(event.position().x()))
        if chapter is not None:
            self.seek_requested.emit(float(chapter[0]))


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
        self._i18n = Retranslator()
        self._setup_ui()
        self._i18n.bind()
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
        self._rewind_btn.setProperty("role", "icon-button")
        self._i18n.text(self._rewind_btn, "tooltip_rewind", "setToolTip")
        self._rewind_btn.clicked.connect(lambda: self._seek_relative(-10))
        controls.addWidget(self._rewind_btn)

        self._play_btn = QPushButton("▶")
        self._play_btn.setProperty("role", "icon-button")
        self._i18n.text(self._play_btn, "tooltip_play", "setToolTip")
        self._play_btn.clicked.connect(self._toggle_play)
        controls.addWidget(self._play_btn)

        self._forward_btn = QPushButton("10s ⏭")
        self._forward_btn.setProperty("role", "icon-button")
        self._i18n.text(self._forward_btn, "tooltip_forward", "setToolTip")
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
        self._time_label.setProperty("size", "small")
        self._time_label.setFixedWidth(90)
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        controls.addWidget(self._time_label)

        layout.addLayout(controls)

        # Chapter ticks under the slider (set_chapters), kept lined up with
        # its groove whenever the slider moves or resizes.
        self._marks = _ChapterMarks(self)
        self._marks.setVisible(False)
        self._marks.seek_requested.connect(self.seek_to)
        self._marks_row = QHBoxLayout()
        self._marks_row.setContentsMargins(0, 0, 0, 0)
        self._marks_row.addWidget(self._marks)
        layout.addLayout(self._marks_row)
        self._fallback_duration = 0.0
        self._chapter_marks: list[tuple[float, str]] = []
        self._slider.installEventFilter(self)

        # Row 2: speed + volume
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        speed_label = self._i18n.text(QLabel(), "label_speed")
        speed_label.setProperty("role", "muted")
        speed_label.setProperty("size", "small")
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

        vol_label = self._i18n.text(QLabel(), "label_volume")
        vol_label.setProperty("role", "muted")
        vol_label.setProperty("size", "small")
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

    def set_chapters(self, chapters: list, fallback_duration: float | None = None) -> None:
        """Show a tick per chapter (``{"start", "title"}`` dicts) on the
        timeline. *fallback_duration* places them before the media reports
        its own duration (or when there is no media at all)."""
        self._fallback_duration = float(fallback_duration or 0.0)
        marks: list[tuple[float, str]] = []
        for item in chapters:
            try:
                marks.append((float(item.get("start", 0)), str(item.get("title", ""))))
            except (TypeError, ValueError, AttributeError):
                continue
        self._chapter_marks = marks
        self._update_marks()

    def _update_marks(self) -> None:
        duration = self._duration if self._duration > 0 else self._fallback_duration
        self._marks.set_chapters(self._chapter_marks, duration)
        self._sync_marks_geometry()

    def _sync_marks_geometry(self) -> None:
        """Line the tick strip up with the slider's groove: same left and
        right edges as the slider, inset by half its handle."""
        geometry = self._slider.geometry()
        if geometry.width() <= 0:
            return
        self._marks_row.setContentsMargins(
            geometry.x(), 0, max(0, self.width() - geometry.right() - 1), 0,
        )
        option = QStyleOptionSlider()
        self._slider.initStyleOption(option)
        handle = self._slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self._slider,
        )
        self._marks.set_inset(max(0, handle.width() // 2))

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 — Qt override
        if obj is self._slider and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._sync_marks_geometry()
        return super().eventFilter(obj, event)

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
        self._update_marks()

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
