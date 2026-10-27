"""Qt side of the "new recording finished" offer: watches the recurring
source folder (``Config.source_folder``, e.g. where Zoom saves its
recordings) and emits ``recording_ready`` once a new recording there is
complete.

``domain/recording_settle.py`` decides what counts as finished; this
module decides when to look. A ``QFileSystemWatcher`` on the folder and
on each unfinished sub-folder catches new meetings and the moment Zoom
swaps its ``.zoom`` files for the converted media. File growth fires no
directory event, so while a finished-looking entry waits out the settle
window the folder is re-scanned on a timer.
"""

from __future__ import annotations

import os
import time

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from core.logger import get_logger
from domain.recording_settle import SettleTracker, media_for, scan
from utils import SUPPORTED_FORMATS

logger = get_logger(__name__)

# How long a finished-looking recording must stay unchanged before it is
# offered, and how often the folder is re-scanned meanwhile.
_SETTLE_SECONDS = 15.0
_POLL_MS = 3000
# Coalesces a burst of directory events into one scan.
_EVENT_DELAY_MS = 500


class RecordingWatchService(QObject):
    """Watches one recording folder and emits ``recording_ready(path)``
    with the media file of each new, finished recording."""

    recording_ready = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._schedule_scan)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._scan)
        self._folder = ""
        self._tracker = SettleTracker(_SETTLE_SECONDS)

    @property
    def folder(self) -> str:
        return self._folder

    def set_folder(self, folder: str) -> None:
        """Start watching *folder* ("" stops). Recordings already finished
        there become the baseline and are never offered."""
        if folder == self._folder:
            return
        self._unwatch_all()
        self._timer.stop()
        self._folder = folder
        if not folder or not os.path.isdir(folder):
            if folder:
                logger.info("Recording folder not found, not watching: %s", folder)
            return
        snapshots = scan(folder, SUPPORTED_FORMATS)
        self._tracker.baseline(snapshots)
        self._watch(snapshots)

    def stop(self) -> None:
        self.set_folder("")

    def _unwatch_all(self) -> None:
        watched = self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)

    def _watch(self, snapshots) -> None:
        wanted = {self._folder} | {
            path for path in self._tracker.unfinished(snapshots) if os.path.isdir(path)
        }
        current = set(self._watcher.directories())
        stale = sorted(current - wanted)
        fresh = sorted(wanted - current)
        if stale:
            self._watcher.removePaths(stale)
        if fresh:
            self._watcher.addPaths(fresh)

    def _schedule_scan(self, _path: str = "") -> None:
        if self._folder and not self._timer.isActive():
            self._timer.start(_EVENT_DELAY_MS)

    def _scan(self) -> None:
        if not self._folder or not os.path.isdir(self._folder):
            return
        snapshots = scan(self._folder, SUPPORTED_FORMATS)
        for entry in self._tracker.observe(snapshots, time.monotonic()):
            media = media_for(entry, SUPPORTED_FORMATS)
            if media is not None:
                logger.info("New recording finished: %s", media)
                self.recording_ready.emit(str(media))
        self._watch(snapshots)
        if self._tracker.is_settling():
            self._timer.start(_POLL_MS)
