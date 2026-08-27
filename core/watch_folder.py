"""Qt side of the watch-folder feature (B5b,
docs/IMPROVEMENT_PLAN_2026-08.ru.md): a ``QFileSystemWatcher`` plus a
debounce so a file still being copied/written isn't queued mid-write.

``domain/watch_folder.py::new_files()`` decides *which* files are new;
this module decides *when* a new file is safe to hand off — after its
size has held steady for ``_DEBOUNCE_MS``, checked by re-listing the
directory on a timer rather than trusting a single filesystem event
(some platforms fire ``directoryChanged`` more than once for one write,
others only at the start).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Set

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from core.logger import get_logger
from domain.watch_folder import content_fingerprint, new_files
from utils import is_supported_format

logger = get_logger(__name__)

_DEBOUNCE_MS = 2000


class WatchFolderService(QObject):
    """Watches one local directory (top level only, no recursion) and
    emits ``file_found(path)`` once each new supported file's size has
    been stable for ``_DEBOUNCE_MS``.
    """

    file_found = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._recheck)
        self._folder: str = ""
        self._seen: Set[str] = set()
        self._pending_sizes: Dict[str, int] = {}

    def set_folder(self, folder: str) -> None:
        """Switch the watched directory. Debounce state (files mid-
        stabilization) from the previous folder is discarded — it
        describes files that no longer matter once watching moves
        elsewhere; already-queued fingerprints in ``_seen`` are kept, so
        re-pointing back at the same folder doesn't re-queue everything."""
        watched = self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)
        self._timer.stop()
        self._pending_sizes.clear()
        self._folder = folder
        if folder and os.path.isdir(folder):
            self._watcher.addPath(folder)
            self._on_directory_changed(folder)

    def stop(self) -> None:
        self.set_folder("")

    def _on_directory_changed(self, _path: str) -> None:
        if not self._folder or not os.path.isdir(self._folder):
            return
        try:
            listing = [
                p for p in Path(self._folder).iterdir()
                if p.is_file() and is_supported_format(str(p))
            ]
        except OSError as exc:
            logger.warning("Watch folder unreadable (%s): %s", self._folder, exc)
            return

        candidates = new_files(self._seen, listing)
        current_paths = {str(p) for p in candidates}
        # A path pending stabilization that no longer appears (moved,
        # deleted, renamed mid-write) has nothing left to confirm.
        for stale in set(self._pending_sizes) - current_paths:
            del self._pending_sizes[stale]

        for path in candidates:
            key = str(path)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            previous = self._pending_sizes.get(key)
            if previous is not None and previous == size:
                self._pending_sizes.pop(key)
                self._seen.add(content_fingerprint(path))
                self.file_found.emit(key)
            else:
                self._pending_sizes[key] = size

        if self._pending_sizes:
            self._timer.start(_DEBOUNCE_MS)

    def _recheck(self) -> None:
        self._on_directory_changed(self._folder)
