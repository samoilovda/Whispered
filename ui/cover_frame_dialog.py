"""Pick a still frame from the loaded video for a cover photo slot.

Frame extraction shells out to FFmpeg (``covers.frames``) and therefore
runs on a ``BaseWorker`` QThread — the dialog only collects the timestamp
and shows candidate thumbnails.  ``.exec()`` runs a nested event loop, so
the worker's queued signals still deliver while the dialog is open.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core.base_worker import BaseWorker
from core.i18n import tr
from core.logger import get_logger
from covers.frames import extract_candidates, extract_frame
from ui.i18n_helpers import Retranslator

logger = get_logger(__name__)

_CANDIDATE_COUNT = 12
_THUMB_W = 160


class CandidateWorker(BaseWorker):
    """Extract evenly-spaced still frames for the thumbnail gallery."""

    ready = pyqtSignal(list)          # list[Path]
    failed = pyqtSignal(str)

    def __init__(self, video: str, directory: str, parent=None) -> None:
        super().__init__(parent)
        self._video = video
        self._directory = directory

    def _execute(self) -> None:
        paths = extract_candidates(
            self._video,
            count=_CANDIDATE_COUNT,
            directory=self._directory,
            cancel=self.is_cancelled,
        )
        if not self.is_cancelled():
            self._emit_terminal(self.ready, paths)

    def _on_error(self, msg: str) -> None:
        self._emit_terminal(self.failed, msg)


class FrameGrabWorker(BaseWorker):
    """Extract one full-resolution still at a chosen timestamp."""

    ready = pyqtSignal(str)           # PNG path
    failed = pyqtSignal(str)

    def __init__(
        self, video: str, time_sec: float, directory: str, parent=None
    ) -> None:
        super().__init__(parent)
        self._video = video
        self._time_sec = time_sec
        self._directory = directory

    def _execute(self) -> None:
        path = extract_frame(
            self._video,
            self._time_sec,
            directory=self._directory,
            cancel=self.is_cancelled,
        )
        if not self.is_cancelled():
            self._emit_terminal(self.ready, str(path))

    def _on_error(self, msg: str) -> None:
        self._emit_terminal(self.failed, msg)


class CoverFrameDialog(QDialog):
    """Returns a chosen timestamp (seconds) via ``selected_time`` on accept."""

    def __init__(
        self,
        video: str,
        playhead: float,
        duration: float,
        work_dir: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._i18n = Retranslator()
        self._video = video
        self._work_dir = work_dir
        self._worker: CandidateWorker | None = None
        self.selected_time: float = max(0.0, playhead)

        self._i18n.text(self, "cover_frame_picker", "setWindowTitle")
        root = QVBoxLayout(self)

        hint = self._i18n.text(QLabel(), "cover_frame_picker_hint")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        root.addWidget(hint)

        time_row = QHBoxLayout()
        time_row.addWidget(self._i18n.text(QLabel(), "cover_frame_time_label"))
        self._time_spin = QDoubleSpinBox()
        self._time_spin.setDecimals(2)
        self._time_spin.setSingleStep(0.5)
        self._time_spin.setSuffix(" s")
        self._time_spin.setRange(0.0, duration if duration > 0 else 86400.0)
        self._time_spin.setValue(self.selected_time)
        time_row.addWidget(self._time_spin)
        self._now_btn = self._i18n.text(QPushButton(), "cover_frame_use_playhead")
        self._now_btn.clicked.connect(
            lambda: self._time_spin.setValue(max(0.0, playhead))
        )
        time_row.addWidget(self._now_btn)
        time_row.addStretch()
        root.addLayout(time_row)

        self._candidates_btn = self._i18n.text(
            QPushButton(), "cover_frame_load_candidates"
        )
        self._candidates_btn.clicked.connect(self._load_candidates)
        root.addWidget(self._candidates_btn)

        self._grid = QGridLayout()
        root.addLayout(self._grid)

        self._status = QLabel()
        self._status.setProperty("role", "muted")
        root.addWidget(self._status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._i18n.bind()

    # ── candidates ─────────────────────────────────────────────────────

    def _load_candidates(self) -> None:
        if self._worker is not None:
            return
        self._candidates_btn.setEnabled(False)
        self._status.setText(tr("cover_frame_extracting"))
        self._worker = CandidateWorker(self._video, self._work_dir, parent=self)
        self._worker.ready.connect(self._on_candidates)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_candidates(self, paths: list[Path]) -> None:
        self._status.clear()
        _, duration = _safe_probe(self._video)
        step = duration / (len(paths) + 1) if duration and paths else 1.0
        for index, path in enumerate(paths):
            timestamp = (index + 1) * step
            thumb = QLabel()
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                thumb.setPixmap(
                    pixmap.scaledToWidth(
                        _THUMB_W, Qt.TransformationMode.SmoothTransformation
                    )
                )
            thumb.setCursor(Qt.CursorShape.PointingHandCursor)
            thumb.mousePressEvent = (  # type: ignore[method-assign]
                lambda _event, t=timestamp: self._pick_and_accept(t)
            )
            self._grid.addWidget(thumb, index // 4, index % 4)
        self._reap_worker()

    def _on_failed(self, message: str) -> None:
        self._status.setText(message)
        self._candidates_btn.setEnabled(True)
        self._reap_worker()

    def _reap_worker(self) -> None:
        if self._worker is not None:
            self._worker.wait(2000)
            self._worker = None

    def _pick_and_accept(self, timestamp: float) -> None:
        self.selected_time = timestamp
        self._accept()

    def _accept(self) -> None:
        self.selected_time = self._time_spin.value()
        self.accept()

    def closeEvent(self, event) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(2000)
            self._worker = None
        super().closeEvent(event)


def _safe_probe(video: str) -> tuple[float, float]:
    try:
        from video_input import probe_video

        return probe_video(video)
    except Exception as exc:
        logger.warning("probe_video failed for %s: %s", video, exc)
        return 30.0, 0.0
