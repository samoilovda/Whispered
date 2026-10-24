"""
Whispered - YouTube upload queue worker

Runs ``application.youtube_autoupload`` (what ``tools/youtube_autoupload.py``
does) off the GUI thread: uploads every queued record not on YouTube yet,
or with *update* pushes the queued texts to videos already uploaded.
Lives in ``ui/`` because ``core/`` may not import ``application/``.
Cancellable between chunks; an interrupted upload resumes on the next run.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from application.youtube_autoupload import QueuedUpload, find_queued, run_queue, run_updates
from core import youtube_oauth, youtube_upload
from core.base_worker import BaseWorker
from core.logger import get_logger

logger = get_logger(__name__)


class YouTubeQueueWorker(BaseWorker):
    """Run the upload queue under *root* (the app's output folder).

    Signals: ``progress(QueuedUpload, percent)`` and ``uploaded(QueuedUpload,
    UploadRecord)`` per record, then exactly one of ``done(list[Outcome])``
    or ``failed(message)``."""

    progress = pyqtSignal(object, int)
    uploaded = pyqtSignal(object, object)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        root: Path,
        *,
        client_id: str,
        client_secret: str,
        update: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._root = root
        self._client_id = client_id
        self._client_secret = client_secret
        self._update = update

    def _on_progress(self, item: QueuedUpload, sent: int, total: int) -> None:
        percent = int(sent * 100 / total) if total else 100
        self.progress.emit(item, min(percent, 100))

    def _execute(self) -> None:
        client = youtube_upload.UploadClient(
            youtube_oauth.TokenProvider(self._client_id, self._client_secret))
        items = find_queued(self._root)
        if self._update:
            outcomes = run_updates(items, client)
        else:
            outcomes = run_queue(
                items, client,
                on_progress=self._on_progress,
                on_uploaded=lambda item, record: self.uploaded.emit(item, record),
                is_cancelled=self.is_cancelled,
            )
        self._emit_terminal(self.done, outcomes)

    def _on_error(self, msg: str) -> None:
        self._emit_terminal(self.failed, msg)
