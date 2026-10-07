"""
Whispered - YouTube upload worker

Runs ``core.youtube_upload.UploadClient`` off the GUI thread. Cancellable
between chunks (and during back-off waits); an interrupted upload leaves its
session file behind so it can be continued.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from core import youtube_oauth, youtube_upload
from core.base_worker import BaseWorker
from core.logger import get_logger
from domain.youtube_publish import PublishPackage

logger = get_logger(__name__)


class YouTubeUploadWorker(BaseWorker):
    """Upload one package. Signals: ``progress(percent, sent, total)``,
    then exactly one of ``uploaded(UploadRecord)``, ``cancelled()``,
    ``failed(message, relogin)`` — *relogin* is True when the stored login
    is no longer valid and the account has to be connected again.
    ``thumbnail_warning(message)`` may precede ``uploaded``."""

    progress = pyqtSignal(int, object, object)
    thumbnail_warning = pyqtSignal(str)
    uploaded = pyqtSignal(object)
    cancelled = pyqtSignal()
    failed = pyqtSignal(str, bool)

    def __init__(
        self,
        package: PublishPackage,
        *,
        client_id: str,
        client_secret: str,
        pending_path: Path,
        record_path: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._package = package
        self._client_id = client_id
        self._client_secret = client_secret
        self._pending_path = pending_path
        self._record_path = record_path

    def _on_progress(self, sent: int, total: int) -> None:
        percent = int(sent * 100 / total) if total else 0
        self.progress.emit(min(percent, 100), sent, total)

    def _execute(self) -> None:
        tokens = youtube_oauth.TokenProvider(self._client_id, self._client_secret)
        client = youtube_upload.UploadClient(tokens)
        try:
            result = client.publish(
                self._package,
                pending_path=self._pending_path,
                record_path=self._record_path,
                on_progress=self._on_progress,
                is_cancelled=self.is_cancelled,
            )
        except youtube_upload.UploadCancelled:
            self._emit_terminal(self.cancelled)
            return
        except youtube_oauth.YouTubeAuthExpired as exc:
            self._emit_terminal(self.failed, str(exc), True)
            return
        except (youtube_oauth.YouTubeAuthError, youtube_upload.YouTubeUploadError) as exc:
            self._emit_terminal(self.failed, str(exc), False)
            return
        if result.thumbnail_warning:
            self.thumbnail_warning.emit(result.thumbnail_warning)
        self._emit_terminal(self.uploaded, result.record)

    def _on_error(self, msg: str) -> None:
        self._emit_terminal(self.failed, msg, False)
