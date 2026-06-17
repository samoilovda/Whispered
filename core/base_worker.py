"""
Whispered – Base QThread worker
Common cancel/error infrastructure shared by all background workers.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QThread

from core.logger import get_logger

logger = get_logger(__name__)


class BaseWorker(QThread):
    """QThread base class providing thread-safe cancellation and error safety.

    Subclasses implement ``_execute()`` with their business logic and
    ``_on_error(msg)`` to emit their specific error signal.  ``run()``
    wraps ``_execute()`` in a try/except so an unhandled exception is
    caught and forwarded rather than silently terminating the thread.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = threading.Event()

    # ── cancellation ────────────────────────────────────────────────────

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    # ── execution template ───────────────────────────────────────────────

    def run(self) -> None:
        try:
            self._execute()
        except Exception as exc:
            logger.exception("%s unhandled error", type(self).__name__)
            self._on_error(str(exc))

    def _execute(self) -> None:
        raise NotImplementedError

    def _on_error(self, msg: str) -> None:
        """Emit the worker-specific error signal. Subclasses must override."""
