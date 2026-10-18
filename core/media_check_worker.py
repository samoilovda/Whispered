"""Background check of a file picked to relink a record's media (T09):
fingerprint and duration — reading and ffprobe never run on the GUI thread."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal

from core.base_worker import BaseWorker
from core.logger import get_logger

logger = get_logger(__name__)


class MediaCheckWorker(BaseWorker):
    """Emits ``checked({"path", "fingerprint", "duration"})`` or ``failed(msg)``."""

    checked = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        self._path = path

    def _on_error(self, msg: str) -> None:
        self._emit_terminal(self.failed, msg)

    def _execute(self) -> None:
        from core.media_identity import media_fingerprint

        fingerprint = media_fingerprint(self._path)  # OSError → failed
        if self._cancelled.is_set():
            return
        duration = None
        try:
            from core.multitrack_audio import probe_media_duration

            duration = probe_media_duration(self._path)
        except Exception as exc:  # noqa: BLE001 - duration is optional evidence
            logger.info("No duration for %s: %s", self._path, exc)
        if self._cancelled.is_set():
            return
        self._emit_terminal(self.checked, {
            "path": self._path, "fingerprint": fingerprint, "duration": duration,
        })
