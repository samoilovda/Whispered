"""
Whispered - YouTube login worker

Runs the browser consent flow of ``core.youtube_oauth`` off the GUI thread.
Cancellable: ``cancel()`` ends the wait for the browser redirect within one
poll interval.
"""

from __future__ import annotations

import webbrowser

from PyQt6.QtCore import pyqtSignal

from core import youtube_oauth
from core.base_worker import BaseWorker
from core.logger import get_logger

logger = get_logger(__name__)


class YouTubeLoginWorker(BaseWorker):
    """Connect the user's YouTube account.

    Signals: ``connected(channel_title)`` on success (the title may be
    empty when it could not be read), ``cancelled()`` when the user denied
    access, cancelled or the browser step timed out, ``failed(message)``
    otherwise. Exactly one of them is emitted per run.
    """

    connected = pyqtSignal(str)
    cancelled = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, client_id: str, client_secret: str, parent=None) -> None:
        super().__init__(parent)
        self._client_id = client_id
        self._client_secret = client_secret

    def _execute(self) -> None:
        try:
            youtube_oauth.authorize(
                self._client_id, self._client_secret,
                open_browser=webbrowser.open,
                is_cancelled=self.is_cancelled,
            )
            provider = youtube_oauth.TokenProvider(self._client_id, self._client_secret)
            title = youtube_oauth.fetch_channel_title(provider.access_token())
        except youtube_oauth.YouTubeAuthCancelled:
            self._emit_terminal(self.cancelled)
            return
        except youtube_oauth.YouTubeAuthError as exc:
            self._emit_terminal(self.failed, str(exc))
            return
        self._emit_terminal(self.connected, title)

    def _on_error(self, msg: str) -> None:
        self._emit_terminal(self.failed, msg)
