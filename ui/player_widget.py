"""
Whispered - Audio Player Widget
QMediaPlayer-based player with position tracking for transcript sync.
Degrades gracefully when Qt Multimedia backend is unavailable.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QSlider,
    QStyle, QStyleOptionSlider, QToolButton, QToolTip, QMenu,
)
from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui import QAction, QActionGroup, QColor, QMouseEvent, QPainter, QPolygon

from core.i18n import tr
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


# Bookmarks are amber in both themes — distinct from the accent-coloured
# chapter ticks, and drawn as a different shape (not colour alone).
_BOOKMARK_COLOR = "#f59e0b"


class _ChapterMarks(QWidget):
    """A thin strip under the position slider with a tick per chapter and
    an amber marker per bookmark. Hovering names the chapter or shows the
    bookmark's note; clicking seeks there; right-clicking a bookmark
    offers to edit its note or delete it. Lined up with the slider's
    groove by PlayerWidget._sync_marks_geometry()."""

    seek_requested = pyqtSignal(float)
    bookmark_menu_requested = pyqtSignal(int, object)   # bookmark id, global QPoint

    _HIT_PX = 6          # how close the pointer must be to a tick

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self.setMouseTracking(True)
        self._chapters: list[tuple[float, str]] = []
        # (seconds, note, bookmark id)
        self._bookmarks: list[tuple[float, str, int]] = []
        self._duration = 0.0
        self._inset = 0              # half the slider handle: where 0 sits

    def set_chapters(self, chapters: list[tuple[float, str]], duration: float) -> None:
        self._chapters = chapters
        self._duration = duration
        self._update_visibility()
        self.update()

    def set_bookmarks(self, bookmarks: list[tuple[float, str, int]]) -> None:
        self._bookmarks = bookmarks
        self._update_visibility()
        self.update()

    def _update_visibility(self) -> None:
        self.setVisible(bool(self._chapters or self._bookmarks) and self._duration > 0)

    def bookmark_at(self, x: int) -> Optional[tuple[float, str, int]]:
        best = None
        best_dist = self._HIT_PX + 1
        for mark in self._bookmarks:
            dist = abs(self.tick_x(mark[0]) - x)
            if dist < best_dist:
                best, best_dist = mark, dist
        return best

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
        if self._duration <= 0 or not (self._chapters or self._bookmarks):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().highlight().color())
        for start, _title in self._chapters:
            painter.drawRect(self.tick_x(start) - 1, 2, 2, self.height() - 2)
        painter.setBrush(QColor(_BOOKMARK_COLOR))
        for start, _note, _id in self._bookmarks:
            x = self.tick_x(start)
            # A small downward triangle hanging from the slider.
            painter.drawPolygon(QPolygon([QPoint(x - 4, 0), QPoint(x + 4, 0), QPoint(x, 7)]))
        painter.end()

    def mouseMoveEvent(self, event):  # noqa: N802 — Qt override
        x = int(event.position().x())
        bookmark = self.bookmark_at(x)
        if bookmark is not None:
            text = f"🔖 {_fmt(bookmark[0])}"
            if bookmark[1]:
                text += f"  {bookmark[1]}"
        else:
            chapter = self.chapter_at(x)
            if chapter is None:
                self.unsetCursor()
                QToolTip.hideText()
                return
            text = f"{_fmt(chapter[0])}  {chapter[1]}"
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        QToolTip.showText(event.globalPosition().toPoint(), text, self)

    def mousePressEvent(self, event):  # noqa: N802 — Qt override
        x = int(event.position().x())
        bookmark = self.bookmark_at(x)
        if bookmark is not None and event.button() == Qt.MouseButton.RightButton:
            self.bookmark_menu_requested.emit(bookmark[2], event.globalPosition().toPoint())
            return
        if bookmark is not None:
            self.seek_requested.emit(float(bookmark[0]))
            return
        chapter = self.chapter_at(x)
        if chapter is not None:
            self.seek_requested.emit(float(chapter[0]))


SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)


def _speed_label(rate: float) -> str:
    return f"{rate:g}×"


class PlayerWidget(QWidget):
    """
    Compact audio/video player (audio only shown), one row:
      - seek −10 s, play/pause, seek +10 s
      - position, slider (click jumps there; hover names the time and
        chapter), chapter ticks under it, duration
      - playback speed menu (0.5×–2×), mute toggle + volume slider
      - signal position_changed_sec(float) emitted ~5 times/s
      - methods seek_to(seconds), seek_relative(delta), speed_step(±1)

    If Qt Multimedia backend is unavailable the widget hides itself and all
    public methods become no-ops so the rest of the app is unaffected.
    """

    position_changed_sec = pyqtSignal(float)  # emitted on timer tick
    seek_requested = pyqtSignal(float)         # internal re-use
    bookmark_menu_requested = pyqtSignal(int, object)  # bookmark id, global QPoint

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
        layout.setContentsMargins(8, 6, 8, 4)
        layout.setSpacing(0)

        controls = QHBoxLayout()
        controls.setSpacing(6)

        self._rewind_btn = QPushButton("−10")
        self._rewind_btn.setProperty("role", "player-skip")
        self._i18n.text(self._rewind_btn, "tooltip_rewind", "setToolTip")
        self._rewind_btn.clicked.connect(lambda: self.seek_relative(-10))
        controls.addWidget(self._rewind_btn)

        self._play_btn = QPushButton("▶")
        self._play_btn.setProperty("role", "play-button")
        self._play_btn.setFixedSize(34, 34)
        self._i18n.text(self._play_btn, "tooltip_play", "setToolTip")
        self._play_btn.clicked.connect(self._toggle_play)
        controls.addWidget(self._play_btn)

        self._forward_btn = QPushButton("+10")
        self._forward_btn.setProperty("role", "player-skip")
        self._i18n.text(self._forward_btn, "tooltip_forward", "setToolTip")
        self._forward_btn.clicked.connect(lambda: self.seek_relative(10))
        controls.addWidget(self._forward_btn)
        controls.addSpacing(6)

        self._pos_label = QLabel("00:00")
        self._pos_label.setProperty("role", "player-time")
        controls.addWidget(self._pos_label)

        # Position slider: a click on the groove jumps there (Qt's default
        # is a page step), hovering shows the time and chapter under the
        # pointer — see eventFilter().
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 1000)
        self._slider.setMouseTracking(True)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._slider.sliderMoved.connect(self._on_slider_moved)
        self._slider_dragging = False
        controls.addWidget(self._slider, stretch=1)

        self._dur_label = QLabel("00:00")
        self._dur_label.setProperty("role", "player-time")
        controls.addWidget(self._dur_label)
        controls.addSpacing(6)

        self._speed_btn = QToolButton()
        self._speed_btn.setProperty("role", "toolbar-icon")
        self._speed_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._speed_menu = QMenu(self._speed_btn)
        self._speed_group = QActionGroup(self)
        self._speed_actions: dict[float, QAction] = {}
        for rate in SPEEDS:
            action = QAction(_speed_label(rate), self._speed_menu)
            action.setCheckable(True)
            action.setChecked(rate == 1.0)
            action.triggered.connect(lambda _c=False, r=rate: self.set_speed(r))
            self._speed_group.addAction(action)
            self._speed_menu.addAction(action)
            self._speed_actions[rate] = action
        self._speed_btn.setMenu(self._speed_menu)
        self._speed_btn.setText(_speed_label(1.0))
        self._speed = 1.0
        controls.addWidget(self._speed_btn)

        self._mute_btn = QPushButton("🔊")
        self._mute_btn.setProperty("role", "icon-button")
        self._mute_btn.setCheckable(True)
        self._mute_btn.toggled.connect(self._on_mute_toggled)
        controls.addWidget(self._mute_btn)

        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(80)
        self._vol_slider.setFixedWidth(72)
        self._vol_slider.valueChanged.connect(self._on_volume_changed)
        controls.addWidget(self._vol_slider)

        layout.addLayout(controls)
        self._retranslate_player()
        self._i18n.call(self._retranslate_player)

        # Chapter ticks under the slider (set_chapters), kept lined up with
        # its groove whenever the slider moves or resizes.
        self._marks = _ChapterMarks(self)
        self._marks.setVisible(False)
        self._marks.seek_requested.connect(self.seek_to)
        self._marks.bookmark_menu_requested.connect(self.bookmark_menu_requested.emit)
        self._marks_row = QHBoxLayout()
        self._marks_row.setContentsMargins(0, 0, 0, 0)
        self._marks_row.addWidget(self._marks)
        layout.addLayout(self._marks_row)
        self._fallback_duration = 0.0
        self._chapter_marks: list[tuple[float, str]] = []
        self._slider.installEventFilter(self)

    def _retranslate_player(self) -> None:
        self._speed_btn.setToolTip(tr("player_speed_tooltip"))
        self._speed_btn.setAccessibleName(tr("player_speed_tooltip"))
        self._mute_btn.setToolTip(tr("player_mute_tooltip"))
        self._mute_btn.setAccessibleName(tr("player_mute_tooltip"))
        self._vol_slider.setToolTip(tr("player_volume_tooltip"))
        self._vol_slider.setAccessibleName(tr("player_volume_tooltip"))
        self._slider.setAccessibleName(tr("player_position"))

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
        player = self._player
        if not self._available or player is None:
            return
        if not filepath:
            player.stop()
            player.setSource(QUrl())
            return
        player.setSource(QUrl.fromLocalFile(filepath))
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

    def seek_relative(self, delta_sec: float) -> None:
        """Move the playhead *delta_sec* seconds (negative = back)."""
        self._seek_relative(delta_sec)

    def set_speed(self, rate: float) -> None:
        self._speed = rate
        self._speed_btn.setText(_speed_label(rate))
        action = self._speed_actions.get(rate)
        if action is not None:
            action.setChecked(True)
        if self._available and self._player:
            self._player.setPlaybackRate(rate)

    def speed(self) -> float:
        return self._speed

    def speed_step(self, direction: int) -> None:
        """One step faster (+1) or slower (−1) along SPEEDS."""
        try:
            index = SPEEDS.index(self._speed)
        except ValueError:
            index = SPEEDS.index(1.0)
        index = min(max(index + direction, 0), len(SPEEDS) - 1)
        self.set_speed(SPEEDS[index])

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

    def set_bookmarks(self, bookmarks: list[tuple[float, str, int]]) -> None:
        """Show a marker per bookmark: (seconds, note, bookmark id)."""
        self._marks.set_bookmarks(list(bookmarks))
        self._sync_marks_geometry()

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
        outer = self.layout().contentsMargins() if self.layout() is not None else None
        left_pad = outer.left() if outer is not None else 0
        right_pad = outer.right() if outer is not None else 0
        self._marks_row.setContentsMargins(
            max(0, geometry.x() - left_pad), 0,
            max(0, self.width() - geometry.right() - 1 - right_pad), 0,
        )
        option = QStyleOptionSlider()
        self._slider.initStyleOption(option)
        handle = self._slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self._slider,
        )
        self._marks.set_inset(max(0, handle.width() // 2))

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 — Qt override
        if obj is self._slider:
            etype = event.type()
            if etype in (QEvent.Type.Resize, QEvent.Type.Move):
                self._sync_marks_geometry()
            elif etype == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
                self._show_hover_time(event)
            elif (
                etype == QEvent.Type.MouseButtonPress
                and isinstance(event, QMouseEvent)
                and event.button() == Qt.MouseButton.LeftButton
                and not self._handle_rect().contains(event.position().toPoint())
            ):
                # Jump to the clicked point, then let the press start a
                # drag from there as usual.
                self._slider.setValue(self._slider_value_at(int(event.position().x())))
        return super().eventFilter(obj, event)

    def _handle_rect(self):
        option = QStyleOptionSlider()
        self._slider.initStyleOption(option)
        return self._slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self._slider,
        )

    def _slider_value_at(self, x: int) -> int:
        handle = self._handle_rect()
        span = max(1, self._slider.width() - handle.width())
        return QStyle.sliderValueFromPosition(
            self._slider.minimum(), self._slider.maximum(),
            max(0, x - handle.width() // 2), span,
        )

    def _media_duration(self) -> float:
        return self._duration if self._duration > 0 else self._fallback_duration

    def _show_hover_time(self, event: QMouseEvent) -> None:
        duration = self._media_duration()
        if duration <= 0:
            return
        seconds = self._slider_value_at(int(event.position().x())) / 1000.0 * duration
        text = _fmt(seconds)
        title = ""
        for start, name in self._chapter_marks:
            if start <= seconds:
                title = name
        if title:
            text = f"{text}  ·  {title}"
        QToolTip.showText(event.globalPosition().toPoint(), text, self._slider)

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
        if not self._available or self._player is None:
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

    def _on_volume_changed(self, value: int):
        if value > 0 and self._mute_btn.isChecked():
            self._mute_btn.setChecked(False)
        self._mute_btn.setText("🔇" if value == 0 or self._mute_btn.isChecked() else "🔊")
        if not self._available or not self._audio_output:
            return
        self._audio_output.setVolume(value / 100.0)

    def _on_mute_toggled(self, muted: bool) -> None:
        self._mute_btn.setText("🔇" if muted else "🔊")
        if self._available and self._audio_output:
            self._audio_output.setMuted(muted)

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
            self._pos_label.setText(_fmt(secs))

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

        # Update time labels
        if not self._slider_dragging:
            self._pos_label.setText(_fmt(pos_sec))
        self._dur_label.setText(_fmt(dur if dur > 0 else self._fallback_duration))

        # Emit signal (throttled by timer interval)
        self.position_changed_sec.emit(pos_sec)


_fmt = format_duration
