"""Live presentation window (N08, pilot behind ``live_presentation_enabled``).

A second window with large text for a room or a screen share. It only
*subscribes* to the running Live session — the same ``SegmentUpdate``
stream the Live tab gets — so there is no second recording or ASR. Drafts
(partial) are marked by italics, a trailing "…" and a "draft" tag, never by
colour alone; a final segment is never replaced by a late draft. Closing the
window leaves the recording running; stopping needs the explicit, confirmed
Stop button.

Keys: ``Space`` pause/resume, ``+``/``-``/``0`` text size, ``F`` full
screen, ``Esc`` leave full screen or close the window.
"""

from __future__ import annotations

from html import escape
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from core.live.contracts import SegmentState, SegmentUpdate
from core.live.presentation import LiveTranscriptModel
from ui.qt_util import must

MIN_POINT_SIZE = 16
MAX_POINT_SIZE = 96
DEFAULT_POINT_SIZE = 30
_MAX_ROWS = 40          # a screen's worth; the full text stays in the Live tab

_ACTIVE = {"starting", "running", "paused", "finalizing"}
_STATE_KEYS = {
    "running": "livepres_state_recording", "paused": "livepres_state_paused",
    "starting": "livepres_state_starting", "finalizing": "livepres_state_finalizing",
    "completed": "livepres_state_stopped", "idle": "livepres_state_stopped",
    "ready": "livepres_state_stopped", "failed": "livepres_state_failed",
    "preflighting": "livepres_state_stopped",
}


class LivePresentationWindow(QWidget):
    pause_requested = pyqtSignal(bool)
    stop_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(tr("livepres_title"))
        self.resize(900, 560)
        self._model = LiveTranscriptModel()
        self._order: list[str] = []
        self._state = "idle"
        self._point_size = DEFAULT_POINT_SIZE
        self._source_state = ""

        root = QVBoxLayout(self)
        header = QHBoxLayout()
        self.state_label = QLabel()
        self.state_label.setAccessibleName(tr("livepres_state_label"))
        header.addWidget(self.state_label)
        header.addStretch()
        self.source_label = QLabel()
        self.source_label.setProperty("role", "muted")
        header.addWidget(self.source_label)
        root.addLayout(header)

        self.text = QTextBrowser()
        self.text.setAccessibleName(tr("livepres_text_label"))
        self.text.setFrameShape(QTextBrowser.Shape.NoFrame)
        root.addWidget(self.text, 1)

        footer = QHBoxLayout()
        self.pause_btn = QPushButton()
        self.pause_btn.clicked.connect(self._toggle_pause)
        footer.addWidget(self.pause_btn)
        self.stop_btn = QPushButton(tr("livepres_stop"))
        self.stop_btn.setToolTip(tr("livepres_stop_tip"))
        self.stop_btn.clicked.connect(self._stop)
        footer.addWidget(self.stop_btn)
        footer.addStretch()
        self.hint = QLabel(tr("livepres_keys"))
        self.hint.setProperty("role", "muted")
        footer.addWidget(self.hint)
        self.close_btn = QPushButton(tr("livepres_close"))
        self.close_btn.setToolTip(tr("livepres_close_tip"))
        self.close_btn.clicked.connect(self.close)
        footer.addWidget(self.close_btn)
        root.addLayout(footer)
        self._apply_font()
        self.set_session_state("idle")

    # ------------------------------------------------------------------ input

    def accept_update(self, update: SegmentUpdate) -> bool:
        """Show *update*; a final is never overwritten by a late draft."""
        if not self._model.accept(update):
            return False
        if update.segment_id not in self._order:
            self._order.append(update.segment_id)
            del self._order[:-_MAX_ROWS]
        self._render()
        return True

    def set_session_state(self, state: str) -> None:
        self._state = str(getattr(state, "value", state))
        self.state_label.setText(tr(_STATE_KEYS.get(self._state, "livepres_state_stopped")))
        active = self._state in _ACTIVE
        self.pause_btn.setEnabled(self._state in {"running", "paused"})
        self.pause_btn.setText(tr("live_resume") if self._state == "paused" else tr("live_pause"))
        self.stop_btn.setEnabled(self._state in {"starting", "running", "paused"})
        self.state_label.setProperty("active", active)
        self._render()

    def set_source_state(self, source: str, state: str) -> None:
        if state in {"failed", "stopped"} and self._state in {"running", "paused"}:
            name = tr("live_source_mic") if source == "mic" else tr("live_zoom")
            self._source_state = tr("livepres_source_lost", source=name)
        elif self._source_state and state in {"running", "starting"}:
            self._source_state = ""
        self._update_source_label()

    def reset(self) -> None:
        self._model.clear()
        self._order.clear()
        self._source_state = ""
        self._render()

    # ------------------------------------------------------------------ view

    def plain_text(self) -> str:
        return self.text.toPlainText()

    def _source_name(self, update: SegmentUpdate) -> str:
        source = update.segment.speaker or update.segment_id.split(":", 1)[0]
        return tr("live_source_mic") if source == "mic" else tr("live_zoom")

    def _update_source_label(self, last: Optional[SegmentUpdate] = None) -> None:
        parts = []
        if last is not None:
            parts.append(self._source_name(last))
        elif self._order:
            latest = self._model.get(self._order[-1])
            if latest is not None:
                parts.append(self._source_name(latest))
        if self._source_state:
            parts.append(self._source_state)
        self.source_label.setText(" · ".join(parts))

    def _render(self) -> None:
        rows = []
        last: Optional[SegmentUpdate] = None
        for segment_id in self._order:
            update = self._model.get(segment_id)
            if update is None:
                continue
            last = update
            text = escape(update.segment.text.strip())
            if update.state is SegmentState.FINAL:
                rows.append(f"<p>{text}</p>")
            else:
                rows.append(f"<p><i>{text} …</i> <small>[{escape(tr('live_partial'))}]</small></p>")
        if not rows:
            body = f"<p><i>{escape(tr('livepres_waiting'))}</i></p>"
        else:
            body = "".join(rows)
        self.text.setHtml(body)
        bar = must(self.text.verticalScrollBar())
        bar.setValue(bar.maximum())
        self._update_source_label(last)

    def _apply_font(self) -> None:
        font = self.text.font()
        font.setPointSize(self._point_size)
        self.text.setFont(font)
        must(self.text.document()).setDefaultFont(font)

    def point_size(self) -> int:
        return self._point_size

    def set_point_size(self, size: int) -> None:
        self._point_size = max(MIN_POINT_SIZE, min(MAX_POINT_SIZE, size))
        self._apply_font()
        self._render()

    # ------------------------------------------------------------------ actions

    def _toggle_pause(self) -> None:
        if self._state in {"running", "paused"}:
            self.pause_requested.emit(self._state == "running")

    def _stop(self) -> None:
        if self._state not in {"starting", "running", "paused"}:
            return
        reply = QMessageBox.question(
            self, tr("livepres_stop"), tr("livepres_stop_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.stop_requested.emit()

    def keyPressEvent(self, event: Optional[QKeyEvent]) -> None:  # noqa: N802 - Qt override
        if event is None:
            return
        key = event.key()
        if key == Qt.Key.Key_Space:
            self._toggle_pause()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.set_point_size(self._point_size + 4)
        elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.set_point_size(self._point_size - 4)
        elif key == Qt.Key.Key_0:
            self.set_point_size(DEFAULT_POINT_SIZE)
        elif key == Qt.Key.Key_F:
            self.setWindowState(self.windowState() ^ Qt.WindowState.WindowFullScreen)
        elif key == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.close()
        else:
            super().keyPressEvent(event)
