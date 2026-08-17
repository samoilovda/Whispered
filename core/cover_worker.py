"""Cancellable QThread orchestration for the cover pipeline."""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import pyqtSignal

from core.base_worker import BaseWorker


class CoverWorker(BaseWorker):
    progress = pyqtSignal(int, str)
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, operation: Callable[..., Any], *args, parent=None, **kwargs):
        super().__init__(parent)
        self.operation, self.args, self.kwargs = operation, args, kwargs

    def _execute(self) -> None:
        if self.is_cancelled():
            return
        kwargs = dict(self.kwargs)
        kwargs.setdefault("cancel", self.is_cancelled)
        try:
            payload = self.operation(*self.args, **kwargs)
        except TypeError as exc:
            if "cancel" not in str(exc):
                raise
            kwargs.pop("cancel", None)
            payload = self.operation(*self.args, **kwargs)
        if not self.is_cancelled():
            self.result.emit(payload)

    def _on_error(self, msg: str) -> None:
        self.error.emit(msg)


class FrameExtractWorker(CoverWorker):
    pass


class TileDetectWorker(CoverWorker):
    pass


class RestoreWorker(CoverWorker):
    pass


class ProviderWorker(CoverWorker):
    pass
