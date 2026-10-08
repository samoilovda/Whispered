"""Choose a speaker photo for a cover slot among prepared variants.

Shows four stills per participant of the loaded video-call recording
(``covers.speaker_photos``), best first, and "Find 4 new variants" for a
new round of moments. Finding runs on a ``BaseWorker`` QThread; the
result is cached in the record's folder so reopening is instant. Like
the other modal ``.exec()`` dialogs it is rebuilt on every open and not
retranslated (see CLAUDE.md, i18n).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.base_worker import BaseWorker
from core.i18n import tr
from core.logger import get_logger
from covers.speaker_photos import CandidateSet, PhotoCandidate, find_candidates, load_candidates

logger = get_logger(__name__)

_THUMB_W = 220


class CandidateSearchWorker(BaseWorker):
    """Run one round of ``find_candidates`` off the GUI thread."""

    ready = pyqtSignal(object)        # CandidateSet
    failed = pyqtSignal(str)
    percent = pyqtSignal(int)

    def __init__(self, video: str, out_dir: str, duration: float,
                 previous: Optional[CandidateSet], parent=None) -> None:
        super().__init__(parent)
        self._video = video
        self._out_dir = out_dir
        self._duration = duration
        self._previous = previous

    def _execute(self) -> None:
        result = find_candidates(
            self._video, self._out_dir, duration=self._duration,
            previous=self._previous, cancel=self.is_cancelled,
            progress=self.percent.emit,
        )
        if not self.is_cancelled():
            self._emit_terminal(self.ready, result)

    def _on_error(self, msg: str) -> None:
        self._emit_terminal(self.failed, msg)


class SpeakerPhotoDialog(QDialog):
    """``selected`` holds the chosen ``PhotoCandidate`` after ``exec()``."""

    def __init__(self, video: str, duration: float, out_dir: str, registry=None,
                 parent=None) -> None:
        super().__init__(parent)
        # Owns the search thread's lifetime (core.worker_registry): a search
        # still running when the dialog closes is retained until it ends.
        self._registry = registry
        self.setWindowTitle(tr("cover_variants_title"))
        self.selected: Optional[PhotoCandidate] = None
        self._video = video
        self._duration = duration
        self._out_dir = out_dir
        self._result: Optional[CandidateSet] = load_candidates(out_dir, video)
        self._worker: Optional[CandidateSearchWorker] = None

        layout = QVBoxLayout(self)
        hint = QLabel(tr("cover_variants_hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._status = QLabel()
        self._status.setProperty("role", "muted")
        layout.addWidget(self._status)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.hide()
        layout.addWidget(self._progress)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        layout.addWidget(self._grid_host)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._more = QPushButton(tr("cover_variants_more"))
        self._more.clicked.connect(self._search)
        buttons.addButton(self._more, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self._result is not None:
            self._show(self._result)
        else:
            self._search()

    def _search(self) -> None:
        if self._worker is not None:
            return
        self._more.setEnabled(False)
        self._status.setText(tr("cover_variants_searching"))
        self._progress.setValue(0)
        self._progress.show()
        worker = CandidateSearchWorker(
            self._video, self._out_dir, self._duration, self._result,
            parent=None if self._registry is not None else self,
        )
        if self._registry is not None:
            self._registry.register(worker, name="cover_speaker_photos")
        worker.percent.connect(self._progress.setValue)
        worker.ready.connect(self._on_ready)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _on_ready(self, result: CandidateSet) -> None:
        self._reap()
        self._result = result
        self._show(result)

    def _on_failed(self, message: str) -> None:
        self._reap()
        logger.warning("Speaker photo search failed: %s", message)
        self._status.setText(message)

    def _reap(self) -> None:
        self._worker = None
        self._progress.hide()
        self._more.setEnabled(True)

    def _show(self, result: CandidateSet) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()
        if not result.candidates:
            self._status.setText(tr("cover_variants_none"))
            return
        self._status.clear()
        for row, participant in enumerate(result.participants):
            self._grid.addWidget(
                QLabel(tr("cover_variants_participant", n=participant + 1)), row, 0
            )
            for column, candidate in enumerate(result.for_participant(participant)):
                pixmap = QPixmap(candidate.path)
                button = QPushButton()
                button.setIcon(QIcon(pixmap))
                height = round(_THUMB_W * pixmap.height() / max(1, pixmap.width()))
                button.setIconSize(QSize(_THUMB_W, height))
                button.setToolTip(_format_time(candidate.time))
                button.clicked.connect(
                    lambda _checked=False, c=candidate: self._pick(c)
                )
                self._grid.addWidget(button, row, column + 1)

    def _pick(self, candidate: PhotoCandidate) -> None:
        self.selected = candidate
        self.accept()

    def done(self, result: int) -> None:
        # Closing mid-search: stop the worker before the dialog goes away.
        if self._worker is not None:
            for signal in (self._worker.ready, self._worker.failed, self._worker.percent):
                try:
                    signal.disconnect()
                except TypeError:
                    pass
            self._worker.cancel()
            if self._registry is None:
                self._worker.wait(5000)
            self._worker = None
        super().done(result)


def _format_time(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def candidate_dir(artifact_dir: Optional[Path], fallback: str) -> str:
    """Where a record's variants are cached: its folder, or a scratch dir
    before the record has one."""
    from covers.speaker_photos import CANDIDATE_DIR

    return str((artifact_dir or Path(fallback)) / CANDIDATE_DIR)
