"""How much a record's generated files take (UI/UX audit 2026-10-18,
UX-16): shown before deleting, computed off the GUI thread."""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from core.base_worker import BaseWorker


def folder_usage(path: "str | Path") -> tuple[int, int]:
    """(bytes, files) under *path*; (0, 0) when it does not exist.
    Symlinks are not followed."""
    total = files = 0
    root = Path(path)
    if not root.is_dir():
        return 0, 0
    for dirpath, _dirs, names in os.walk(root, followlinks=False):
        for name in names:
            try:
                total += (Path(dirpath) / name).lstat().st_size
                files += 1
            except OSError:
                continue
    return total, files


class DiskUsageWorker(BaseWorker):
    """Emits ``measured(path, bytes, files)`` or ``failed(msg)``."""

    measured = pyqtSignal(str, object, int)
    failed = pyqtSignal(str)

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        self._path = path

    def _on_error(self, msg: str) -> None:
        self._emit_terminal(self.failed, msg)

    def _execute(self) -> None:
        size, files = folder_usage(self._path)
        if not self._cancelled.is_set():
            self._emit_terminal(self.measured, self._path, size, files)
