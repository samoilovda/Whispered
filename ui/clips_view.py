"""Short clips (N07 pilot): choose a range, check it, render MP4 + subtitles.

The range is typed in or taken from the playhead or the passage under the
transcript cursor, on the *source* timeline or on the *edited* one (after
the Cut tab's removals) — the two are named and never mixed. Preview plays
the range; rendering is frame-accurate, never overwrites the source, and a
cancelled render leaves nothing behind. The first delivery keeps the
original aspect ratio (a 9:16 reframe is a later step).
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from application import clips as clips_app
from core.i18n import tr
from core.logger import get_logger
from core.worker_registry import WorkerRegistry
from domain import clips as c
from domain import subtitles as sub
from ui.clip_worker import ClipExportWorker
from ui.i18n_helpers import Retranslator

logger = get_logger(__name__)


def _fmt(seconds: float) -> str:
    return sub.format_time(seconds)


class ClipsView(QWidget):
    listen_requested = pyqtSignal(float, float, bool)
    listen_sequence = pyqtSignal(list)          # [(start, end), ...] played one after the other
    content_changed = pyqtSignal()
    message = pyqtSignal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._i18n = Retranslator()
        self._set = c.ClipSet()
        self._revision = ""
        self._duration = 0.0
        self._record_id: Optional[int] = None
        self._source_path: Optional[str] = None
        self._unsupported = False
        self._worker: Optional[ClipExportWorker] = None
        self._removed: list[tuple[int, c.ClipDraft]] = []      # for "undo remove"
        self._registry = WorkerRegistry(parent=self)
        # Host hooks (set by MainWindow): where the playhead is, the passage
        # under the transcript cursor, the media file, the segments left
        # after the cut, and the subtitle cues to cut for a clip.
        self.playhead: Callable[[], float] = lambda: 0.0
        self.passage: Callable[[], Optional[tuple[float, float]]] = lambda: None
        self.source: Callable[[], str] = lambda: ""
        self.kept: Callable[[], list] = lambda: []
        self.cues: Callable[[], list] = lambda: []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        # The form and the list scroll when the tab is short (the minimum
        # window leaves ~300 px); the actions below them stay in reach.
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("role", "muted")
        layout.addWidget(self.status)

        form = QFormLayout()
        self.title_edit = QLineEdit()
        self._i18n.form_row(form, "clips_title", self.title_edit)
        self.timeline = QComboBox()
        self.timeline.addItem("", c.SOURCE)
        self.timeline.addItem("", c.EDITED)
        self._i18n.form_row(form, "clips_timeline", self.timeline)
        self.start_edit, start_row = self._time_row("clips_in")
        self._i18n.form_row(form, "clips_in", start_row)
        self.end_edit, end_row = self._time_row("clips_out")
        self._i18n.form_row(form, "clips_out", end_row)
        layout.addLayout(form)

        row = QHBoxLayout()
        self.passage_btn = self._button(row, "clips_from_passage", self._from_passage)
        self.preview_btn = self._button(row, "clips_preview", self._preview)
        self.add_btn = self._button(row, "clips_add", self._add)
        self.update_btn = self._button(row, "clips_update", self._update_selected)
        row.addStretch()
        layout.addLayout(row)

        self.list = QListWidget()
        self.list.setMinimumHeight(120)
        self.list.currentRowChanged.connect(self._row_changed)
        layout.addWidget(self.list, stretch=1)
        bottom = QHBoxLayout()
        self.export_btn = self._button(bottom, "clips_export", self._export)
        self.cancel_btn = self._button(bottom, "btn_cancel", self.cancel)
        self.cancel_btn.setVisible(False)
        self.duplicate_btn = self._button(bottom, "clips_duplicate", self._duplicate)
        self.remove_btn = self._button(bottom, "clips_remove", self._remove)
        self.undo_btn = self._button(bottom, "clips_undo_remove", self._undo_remove)
        bottom.addStretch()
        outer.addLayout(bottom)
        self._i18n.add(self._retranslate_fields)
        self._render()
        self.update_passage_availability()
        # State-derived text: the status line, the list rows, the tooltip.
        self._i18n.call(self._render)
        self._i18n.call(self.update_passage_availability)
        self._i18n.bind()

    def _button(self, row, key: str, slot) -> QPushButton:
        btn = self._i18n.text(QPushButton(), key)
        btn.clicked.connect(slot)
        row.addWidget(btn)
        return btn

    def _retranslate_fields(self) -> None:
        """Captions that are not a plain label or button text."""
        self.title_edit.setAccessibleName(tr("clips_title"))
        self.start_edit.setAccessibleName(tr("clips_in"))
        self.end_edit.setAccessibleName(tr("clips_out"))
        self.timeline.setAccessibleName(tr("clips_timeline"))
        self.timeline.setItemText(self.timeline.findData(c.SOURCE), tr("clips_timeline_source"))
        self.timeline.setItemText(self.timeline.findData(c.EDITED), tr("clips_timeline_edited"))
        self.timeline.setToolTip(tr("clips_timeline_tip"))
        self.list.setAccessibleName(tr("clips_list"))

    def _time_row(self, key: str):
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit("00:00:00.000")
        lay.addWidget(edit, 1)
        here = self._i18n.text(QPushButton(), "clips_playhead", tooltip="clips_playhead_tip")
        here.clicked.connect(lambda: self._take_playhead(edit))
        lay.addWidget(here)
        return edit, box

    def _take_playhead(self, edit: QLineEdit) -> None:
        """Put the player's position into *edit*, on the chosen timeline:
        the player runs on the source, so for the edited timeline it is
        converted through the kept segments."""
        position = self.playhead()
        if self.timeline.currentData() == c.EDITED:
            converted = c.source_to_edited(self.kept(), position)
            if converted is None:
                self.message.emit(tr("clips_playhead_in_cut"), "info")
                return
            position = converted
        edit.setText(_fmt(position))

    # ------------------------------------------------------------------ context

    def set_provenance(self, record_id: Optional[int], source_path: Optional[str]) -> None:
        self._record_id = record_id
        self._source_path = source_path
        self._unsupported = False
        self._set = c.ClipSet()
        self._removed = []
        self._reset_draft()
        if isinstance(record_id, int):
            try:
                loaded = clips_app.load_clips(self._dir())
            except Exception as exc:  # noqa: BLE001 - a broken file must not break the tab
                logger.warning("Could not read clips: %s", exc)
                loaded = c.ClipSet()
            if loaded is None:
                self._unsupported = True
            else:
                self._set = loaded
        self._render()
        self.content_changed.emit()

    def set_result(self, result: Any) -> None:
        from domain.artifact_provenance import transcript_revision

        self._duration = float(getattr(result, "duration", 0.0) or 0.0)
        self._revision = transcript_revision(
            result.segments, result.language, getattr(result, "speaker_names", None))
        self._render()
        self.update_passage_availability()

    def _reset_draft(self) -> None:
        """Empty the form: a half-typed clip belongs to the record it was
        typed for."""
        self.title_edit.clear()
        self.start_edit.setText("00:00:00.000")
        self.end_edit.setText("00:00:00.000")
        self.timeline.setCurrentIndex(self.timeline.findData(c.SOURCE))

    def clear(self) -> None:
        self.cancel()
        self._set = c.ClipSet()
        self._removed = []
        self._record_id = None
        self._reset_draft()
        self._render()
        self.content_changed.emit()

    def has_content(self) -> bool:
        return bool(self._set.clips)

    def _dir(self) -> Path:
        from core.paths import artifact_dir

        return artifact_dir(self._record_id if self._record_id is not None else "unsaved",
                            self._source_path or "recording")

    def _save(self) -> None:
        if self._unsupported or not isinstance(self._record_id, int):
            return
        try:
            clips_app.save_clips(self._dir(), self._set)
        except OSError as exc:
            logger.warning("Could not save clips: %s", exc)
            self.message.emit(tr("clips_save_error"), "error")

    # ------------------------------------------------------------------ render

    def _describe(self, clip: c.ClipDraft) -> str:
        line = tr("clips_item", title=clip.title, start=_fmt(clip.start), end=_fmt(clip.end),
                  seconds=f"{clip.duration:.1f}",
                  timeline=tr("clips_timeline_edited" if clip.timeline == c.EDITED
                              else "clips_timeline_source"))
        problems = self._problems(clip)
        if problems:
            return line + f"  ⚠ {tr(f'clips_problem_{problems[0]}')}"
        return line + (f"  ⚠ {tr('clips_stale')}" if self._is_stale(clip) else "")

    def _is_stale(self, clip: c.ClipDraft) -> bool:
        return bool(clip.transcript_revision and self._revision
                    and clip.transcript_revision != self._revision)

    def _problems(self, clip: c.ClipDraft) -> list[str]:
        kept = self.kept()
        return c.check_clip(clip, media_duration=self._duration or None,
                            edited_duration=c.edited_duration(kept) if kept else None)

    def _selected_id(self) -> str:
        row = self.list.currentRow()
        return self._set.clips[row].id if 0 <= row < len(self._set.clips) else ""

    def _render(self) -> None:
        keep = self._selected_id()
        self.list.blockSignals(True)       # rebuilding is not the user picking a clip
        self.list.clear()
        for clip in self._set.clips:
            self.list.addItem(self._describe(clip))
        index = next((i for i, clip in enumerate(self._set.clips) if keep and clip.id == keep), -1)
        self.list.setCurrentRow(index)
        self.list.blockSignals(False)
        busy = self._worker is not None and self._worker.isRunning()
        has = bool(self._set.clips)
        self.export_btn.setEnabled(has and not busy and not self._unsupported)
        self.remove_btn.setEnabled(has and not busy)
        picked = self.list.currentRow() >= 0
        self.update_btn.setEnabled(picked and not busy and not self._unsupported)
        self.duplicate_btn.setEnabled(picked and not busy and not self._unsupported)
        self.undo_btn.setEnabled(bool(self._removed) and not busy and not self._unsupported)
        self.add_btn.setEnabled(not self._unsupported)
        self.cancel_btn.setVisible(busy)
        if self._unsupported:
            text = tr("clips_unsupported")
        elif not has:
            text = tr("clips_empty")
        else:
            text = tr("clips_count", count=len(self._set.clips))
            if any(self._is_stale(clip) for clip in self._set.clips):
                text += " · " + tr("clips_stale")
        self.status.setText(text)

    # ------------------------------------------------------------------ editing

    def draft(self) -> Optional[c.ClipDraft]:
        start, end = sub.parse_time(self.start_edit.text()), sub.parse_time(self.end_edit.text())
        if start is None or end is None:
            self.message.emit(tr("subs_bad_time"), "error")
            return None
        return c.ClipDraft(self.title_edit.text().strip(), start, end, self.timeline.currentData())

    def update_passage_availability(self) -> None:
        """Enable "from passage" only while the transcript has a passage to
        take, and say why not otherwise."""
        has = self.passage() is not None
        self.passage_btn.setEnabled(has)
        self.passage_btn.setToolTip("" if has else tr("clips_no_passage"))

    def _from_passage(self) -> None:
        passage = self.passage()
        if passage is None:
            self.message.emit(tr("clips_no_passage"), "info")
            return
        self.timeline.setCurrentIndex(self.timeline.findData(c.SOURCE))
        self.start_edit.setText(_fmt(passage[0]))
        self.end_edit.setText(_fmt(passage[1]))

    def _preview(self) -> None:
        clip = self.draft()
        if clip is None:
            return
        pieces = c.source_pieces(clip, self.kept())
        if not pieces:
            self.message.emit(tr(f"clips_problem_{c.CLIP_NO_PIECES}"), "error")
            return
        if len(pieces) == 1:
            self.listen_requested.emit(pieces[0][0], pieces[0][1], False)
        else:
            self.listen_sequence.emit(pieces)

    def _add(self) -> None:
        clip = self.draft()
        if clip is None:
            return
        problems = self._problems(clip)
        if problems:
            self.message.emit(tr(f"clips_problem_{problems[0]}"), "error")
            return
        self._set.clips.append(replace(clip, transcript_revision=self._revision, id=c.new_clip_id()))
        self.title_edit.clear()
        self._save()
        self._render()
        self.content_changed.emit()

    def _row_changed(self, row: int) -> None:
        """Picking a clip puts it in the form, ready to change."""
        if 0 <= row < len(self._set.clips):
            clip = self._set.clips[row]
            self.title_edit.setText(clip.title)
            self.timeline.setCurrentIndex(self.timeline.findData(clip.timeline))
            self.start_edit.setText(_fmt(clip.start))
            self.end_edit.setText(_fmt(clip.end))
        self._render_buttons()

    def _render_buttons(self) -> None:
        busy = self._worker is not None and self._worker.isRunning()
        picked = self.list.currentRow() >= 0
        self.update_btn.setEnabled(picked and not busy and not self._unsupported)
        self.duplicate_btn.setEnabled(picked and not busy and not self._unsupported)

    def _update_selected(self) -> None:
        """Save the form into the picked clip: same place in the list, same
        identity, every other clip untouched."""
        row = self.list.currentRow()
        if not 0 <= row < len(self._set.clips):
            return
        clip = self.draft()
        if clip is None:
            return
        problems = self._problems(clip)
        if problems:
            self.message.emit(tr(f"clips_problem_{problems[0]}"), "error")
            return
        old = self._set.clips[row]
        self._set.clips[row] = replace(clip, transcript_revision=self._revision, id=old.id)
        self._save()
        self._render()
        self.content_changed.emit()

    def _duplicate(self) -> None:
        row = self.list.currentRow()
        if not 0 <= row < len(self._set.clips):
            return
        original = self._set.clips[row]
        copy = replace(original, title=tr("clips_copy_title", title=original.title), id=c.new_clip_id())
        self._set.clips.insert(row + 1, copy)
        self._save()
        self._render()
        self.content_changed.emit()

    def _remove(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._set.clips):
            self._removed.append((row, self._set.clips.pop(row)))
            self._save()
            self._render()
            self.content_changed.emit()

    def _undo_remove(self) -> None:
        if not self._removed:
            return
        row, clip = self._removed.pop()
        self._set.clips.insert(min(row, len(self._set.clips)), clip)
        self._save()
        self._render()
        self.content_changed.emit()

    # ------------------------------------------------------------------ render a clip

    def _export(self) -> None:
        row = self.list.currentRow()
        if not 0 <= row < len(self._set.clips):
            self.message.emit(tr("clips_pick_one"), "info")
            return
        clip = self._set.clips[row]
        default = f"{c.safe_file_stem(clip.title)}.mp4"
        path, _ = QFileDialog.getSaveFileName(self, tr("clips_export"), default, "MP4 (*.mp4)")
        if not path:
            return
        if not path.lower().endswith(".mp4"):
            path += ".mp4"
        try:
            plan = clips_app.plan_render(
                clip, source=self.source(), output=path, kept=self.kept(),
                cues=self.cues(), media_duration=self._duration or None)
        except ValueError as exc:
            self.message.emit(tr(f"clips_problem_{exc}"), "error")
            return
        if os.path.exists(path) or (plan.subtitles and os.path.exists(plan.srt_path)):
            reply = QMessageBox.question(self, tr("clips_export"), tr("clips_overwrite"),
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        worker = ClipExportWorker(plan, self)
        worker.progress.connect(lambda text: self.status.setText(text))
        worker.done.connect(self._rendered)
        worker.failed.connect(self._render_failed)
        worker.cancelled.connect(self._render_cancelled)
        self._worker = worker
        self._registry.register(worker, name="clip")
        worker.start()
        self._render()

    def _finish(self) -> None:
        self._worker = None
        self._render()

    def _rendered(self, path: str) -> None:
        self._finish()
        self.message.emit(tr("clips_rendered", name=Path(path).name), "success")

    def _render_failed(self, error: str) -> None:
        self._finish()
        self.message.emit(tr("clips_render_failed", error=error[:200]), "error")

    def _render_cancelled(self) -> None:
        self._finish()
        self.message.emit(tr("clips_cancelled"), "info")

    def cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()

    def shutdown(self) -> None:
        """Shutdownable: stop a running render (a worker that outlives the
        bounded wait is kept alive by the registry until it ends)."""
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        worker.cancel()
        if not worker.wait(5000):
            self._registry.retire(worker)
