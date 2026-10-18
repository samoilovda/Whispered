"""Renders one short clip off the GUI thread (N07)."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal

from application import clips as clips_app
from core.base_worker import BaseWorker
from video_cut import VideoCutCancelled


class ClipExportWorker(BaseWorker):
    """``done(path)`` when the video (and its subtitles) are in place,
    ``failed(message)`` otherwise, ``cancelled()`` after a cancel — in the
    last two nothing is left behind."""

    progress = pyqtSignal(str)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, plan: clips_app.RenderPlan, parent=None) -> None:
        super().__init__(parent)
        self._plan = plan

    def _on_error(self, msg: str) -> None:
        self._emit_terminal(self.failed, msg)

    def _execute(self) -> None:
        try:
            clips_app.render(self._plan, on_progress=self.progress.emit,
                             should_cancel=self.is_cancelled)
        except VideoCutCancelled:
            self._emit_terminal(self.cancelled)
            return
        self._emit_terminal(self.done, self._plan.output)
