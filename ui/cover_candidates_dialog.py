"""Choose the cover's speaker photos among prepared variants.

One row per participant of the loaded video-call recording
(``covers.speaker_photos``): four stills, best first, the cover slot the
row fills ("left photo" / "right photo", swappable), and "4 more" for new
moments of just that participant — the other rows stay as they are.
"Apply" puts every row's chosen still into its slot at once; a row
without a choice leaves its slot unchanged.

Finding runs on a ``BaseWorker`` QThread owned by the workspace's
registry; results are cached in the record's folder so reopening is
instant. Like the other modal ``.exec()`` dialogs it is rebuilt on every
open and not retranslated (see CLAUDE.md, i18n).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
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
from covers.speaker_photos import (
    PHOTO_SLOTS,
    CandidateSet,
    PhotoCandidate,
    find_candidates,
    load_candidates,
    save_candidates,
)

logger = get_logger(__name__)

_THUMB_W = 200


class CandidateSearchWorker(BaseWorker):
    """Run one round of ``find_candidates`` off the GUI thread."""

    ready = pyqtSignal(object)        # CandidateSet
    failed = pyqtSignal(str)
    percent = pyqtSignal(int)

    def __init__(self, video: str, out_dir: str, duration: float,
                 previous: Optional[CandidateSet],
                 participants: Optional[Iterable[int]] = None, parent=None) -> None:
        super().__init__(parent)
        self._video = video
        self._out_dir = out_dir
        self._duration = duration
        self._previous = previous
        self._participants = list(participants) if participants is not None else None

    def _execute(self) -> None:
        result = find_candidates(
            self._video, self._out_dir, duration=self._duration,
            previous=self._previous, participants=self._participants,
            cancel=self.is_cancelled, progress=self.percent.emit,
        )
        if not self.is_cancelled():
            self._emit_terminal(self.ready, result)

    def _on_error(self, msg: str) -> None:
        self._emit_terminal(self.failed, msg)


def _digest(path: str) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
    except OSError:
        return ""


class SpeakerPhotoDialog(QDialog):
    """After ``exec()`` returns Accepted, ``choices`` maps each cover slot
    that got a new photo to its ``PhotoCandidate``."""

    def __init__(self, video: str, duration: float, out_dir: str,
                 current: Optional[dict[str, str]] = None, registry=None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("cover_variants_title"))
        self.choices: dict[str, PhotoCandidate] = {}
        self._video = video
        self._duration = duration
        self._out_dir = out_dir
        # Owns the search thread's lifetime (core.worker_registry): a search
        # still running when the dialog closes is retained until it ends.
        self._registry = registry if registry is not None else _fallback_registry()
        self._result: Optional[CandidateSet] = load_candidates(out_dir, video)
        self._worker: Optional[CandidateSearchWorker] = None
        self._selected: dict[int, PhotoCandidate] = {}
        self._groups: list[QButtonGroup] = []
        # Slot photos already on the cover, kept under a name with their
        # content digest (application/cover_setup.store_photo).
        self._current = {slot: Path(path).name for slot, path in (current or {}).items() if path}

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
        # The picked still (or the one already on the cover) must stand
        # out from its row, not just get a faint pressed shade.
        self._grid_host.setStyleSheet(
            "QPushButton:checked { border: 3px solid palette(highlight); border-radius: 6px; }"
        )
        self._grid = QGridLayout(self._grid_host)
        layout.addWidget(self._grid_host)

        buttons = QDialogButtonBox()
        self._swap = QPushButton(tr("cover_variants_swap"))
        self._swap.clicked.connect(self._swap_slots)
        buttons.addButton(self._swap, QDialogButtonBox.ButtonRole.ActionRole)
        self._more = QPushButton(tr("cover_variants_more"))
        self._more.clicked.connect(lambda: self._search(None))
        buttons.addButton(self._more, QDialogButtonBox.ButtonRole.ActionRole)
        self._apply = QPushButton(tr("cover_variants_apply"))
        self._apply.setEnabled(False)
        self._apply.clicked.connect(self._accept_choices)
        buttons.addButton(self._apply, QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = QPushButton(tr("btn_cancel"))
        buttons.addButton(cancel, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self._result is not None:
            self._show(self._result)
        else:
            self._search(None)

    # ── Searching ──────────────────────────────────────────────────────

    def _search(self, participants: Optional[list[int]]) -> None:
        if self._worker is not None:
            return
        self._set_busy(True)
        self._status.setText(tr("cover_variants_searching"))
        worker = CandidateSearchWorker(
            self._video, self._out_dir, self._duration, self._result, participants,
        )
        self._registry.register(worker, name="cover_speaker_photos")
        worker.percent.connect(self._progress.setValue)
        worker.ready.connect(self._on_ready)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _set_busy(self, busy: bool) -> None:
        self._progress.setValue(0)
        self._progress.setVisible(busy)
        for button in self._grid_host.findChildren(QPushButton):
            button.setEnabled(not busy)
        self._more.setEnabled(not busy)
        self._swap.setEnabled(not busy and bool(self._result and len(self._result.participants) > 1))

    def _on_ready(self, result: CandidateSet) -> None:
        self._worker = None
        self._result = result
        # A choice survives only while its still is still on offer.
        kept = {c.path for c in result.candidates}
        self._selected = {p: c for p, c in self._selected.items() if c.path in kept}
        self._show(result)
        self._set_busy(False)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        logger.warning("Speaker photo search failed: %s", message)
        self._status.setText(message)
        self._set_busy(False)

    # ── Rows ───────────────────────────────────────────────────────────

    def _slot_label(self, participant: int) -> str:
        slot = self._result.slot_for(participant) if self._result else None
        return tr(f"cover_slot_{slot}") if slot else tr("cover_slot_unused")

    def _show(self, result: CandidateSet) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._groups = []
        if not result.candidates:
            self._status.setText(tr("cover_variants_none"))
            self._update_apply()
            return
        self._status.clear()
        for row, participant in enumerate(result.participants):
            label = QLabel(tr("cover_variants_row", n=participant + 1,
                              slot=self._slot_label(participant)))
            label.setWordWrap(True)
            label.setMinimumWidth(120)
            self._grid.addWidget(label, row, 0)
            group = QButtonGroup(self)
            group.setExclusive(True)
            self._groups.append(group)
            slot = result.slot_for(participant)
            on_cover = self._current.get(slot or "", "")
            for column, candidate in enumerate(result.for_participant(participant)):
                pixmap = QPixmap(candidate.path)
                button = QPushButton()
                button.setCheckable(True)
                button.setIcon(QIcon(pixmap))
                height = round(_THUMB_W * pixmap.height() / max(1, pixmap.width()))
                button.setIconSize(QSize(_THUMB_W, height))
                button.setToolTip(_format_time(candidate.time))
                chosen = self._selected.get(participant)
                if chosen is not None and chosen.path == candidate.path:
                    button.setChecked(True)
                elif chosen is None and on_cover and _digest(candidate.path) in on_cover:
                    button.setChecked(True)   # the photo already on the cover
                group.addButton(button)
                button.clicked.connect(
                    lambda _checked=False, p=participant, c=candidate: self._choose(p, c)
                )
                self._grid.addWidget(button, row, column + 1)
            more = QPushButton(tr("cover_variants_more_one"))
            more.clicked.connect(lambda _checked=False, p=participant: self._search([p]))
            self._grid.addWidget(more, row, 5)
        self._swap.setEnabled(len(result.participants) > 1)
        self._update_apply()

    def _choose(self, participant: int, candidate: PhotoCandidate) -> None:
        self._selected[participant] = candidate
        self._update_apply()

    def _update_apply(self) -> None:
        self._apply.setEnabled(bool(self._selected))

    def _swap_slots(self) -> None:
        """Swap which participant fills the left and the right photo."""
        if self._result is None or len(self._result.participants) < 2:
            return
        first, second = self._result.participants[:2]
        a, b = self._result.slot_for(first), self._result.slot_for(second)
        self._result.slots[first] = b or ""
        self._result.slots[second] = a or ""
        self._show(self._result)

    def _accept_choices(self) -> None:
        if self._result is None:
            return
        for participant, candidate in self._selected.items():
            slot = self._result.slot_for(participant)
            if slot in PHOTO_SLOTS:
                self.choices[slot] = candidate
        try:
            save_candidates(self._out_dir, self._result)   # keep the slot mapping
        except OSError as exc:
            logger.warning("Could not save speaker photo choices: %s", exc)
        self.accept()

    def done(self, result: int) -> None:
        # Closing mid-search: let the registry finish the worker; nothing of
        # it may reach this dialog any more.
        if self._worker is not None:
            for signal in (self._worker.ready, self._worker.failed, self._worker.percent):
                try:
                    signal.disconnect()
                except TypeError:
                    pass
            self._registry.retire(self._worker)
            self._worker = None
        super().done(result)


_FALLBACK_REGISTRY = None


def _fallback_registry():
    """A process-lifetime registry for a dialog opened without one, so a
    search outliving the dialog is never dropped while running."""
    global _FALLBACK_REGISTRY
    if _FALLBACK_REGISTRY is None:
        from core.worker_registry import WorkerRegistry

        _FALLBACK_REGISTRY = WorkerRegistry()
    return _FALLBACK_REGISTRY


def _format_time(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def candidate_dir(artifact_dir: Optional[Path], fallback: str) -> str:
    """Where a record's variants are cached: its folder, or a scratch dir
    before the record has one."""
    from covers.speaker_photos import CANDIDATE_DIR

    return str((artifact_dir or Path(fallback)) / CANDIDATE_DIR)
