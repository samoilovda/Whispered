"""Run one blocking callable off the GUI thread (planning an export,
rebuilding an index). ``done(result)`` or ``failed(message)``; ``cancel()``
is visible to the callable through the ``is_cancelled`` argument it is given."""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import pyqtSignal

from core.base_worker import BaseWorker


class CallWorker(BaseWorker):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[[Callable[[], bool]], Any], parent=None) -> None:
        super().__init__(parent)
        self._fn = fn

    def _on_error(self, msg: str) -> None:
        self._emit_terminal(self.failed, msg)

    def _execute(self) -> None:
        result = self._fn(self.is_cancelled)
        self._emit_terminal(self.done, result)
