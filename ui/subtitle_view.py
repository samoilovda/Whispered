"""Subtitle preparation (N02): cut the transcript to reading limits, fix
timing and wording per cue, split and merge, then export SRT / WebVTT.

An editable layer of its own — nothing here changes the transcript, and the
source video is never touched. The checks (lines, characters, characters
per second, duration, overlap) are editorial recommendations from the
preset, shown beside each cue; an export refuses only what no player could
show (empty cue, end not after start).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from application import subtitle_store
from core.i18n import tr
from core.logger import get_logger
from domain import subtitles as sub
from ui.i18n_helpers import Retranslator
from ui.qt_util import must

logger = get_logger(__name__)

_COL_TIME_IN, _COL_TIME_OUT, _COL_TEXT, _COL_ISSUES = 0, 1, 2, 3


stamp = sub.format_time
parse_stamp = sub.parse_time


class _PresetDialog(QDialog):
    def __init__(self, preset: sub.SubtitlePreset, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("subs_preset"))
        form = QFormLayout(self)
        self.lines = QSpinBox()
        self.lines.setRange(1, 4)
        self.lines.setValue(preset.max_lines)
        form.addRow(tr("subs_max_lines"), self.lines)
        self.chars = QSpinBox()
        self.chars.setRange(15, 100)
        self.chars.setValue(preset.max_chars_per_line)
        form.addRow(tr("subs_max_chars"), self.chars)
        self.cps = QDoubleSpinBox()
        self.cps.setRange(5, 40)
        self.cps.setValue(preset.max_cps)
        form.addRow(tr("subs_max_cps"), self.cps)
        self.min_d = QDoubleSpinBox()
        self.min_d.setRange(0.2, 5)
        self.min_d.setSingleStep(0.1)
        self.min_d.setValue(preset.min_duration)
        form.addRow(tr("subs_min_duration"), self.min_d)
        self.max_d = QDoubleSpinBox()
        self.max_d.setRange(2, 20)
        self.max_d.setValue(preset.max_duration)
        form.addRow(tr("subs_max_duration"), self.max_d)
        note = QLabel(tr("subs_preset_note"))
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def preset(self) -> sub.SubtitlePreset:
        return sub.SubtitlePreset(self.lines.value(), self.chars.value(), self.cps.value(),
                                  self.min_d.value(), self.max_d.value())


class SubtitleView(QWidget):
    listen_requested = pyqtSignal(float, float, bool)
    content_changed = pyqtSignal()
    message = pyqtSignal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._i18n = Retranslator()
        self._track = sub.SubtitleTrack()
        self._segments: list = []
        self._duration = 0.0
        self._revision = ""
        self._record_id: Optional[int] = None
        self._source_path: Optional[str] = None
        self._unsupported = False
        self._filling = False
        self._snapshot: Optional[sub.SubtitleTrack] = None      # the track before the last replace/import

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("role", "muted")
        layout.addWidget(self.status)

        top = QHBoxLayout()
        self.generate_btn = self._button(top, "subs_generate", self._generate)
        self.preset_btn = self._button(top, "subs_preset", self._edit_preset)
        self.import_btn = self._button(top, "subs_import", self._import)
        self.undo_btn = self._button(top, "subs_undo", self._undo)
        top.addStretch()
        self.srt_btn = self._button(top, "subs_export_srt", lambda: self._export("srt"))
        self.vtt_btn = self._button(top, "subs_export_vtt", lambda: self._export("vtt"))
        layout.addLayout(top)

        self.table = QTableWidget(0, 4)
        header = must(self.table.horizontalHeader())
        header.setSectionResizeMode(_COL_TEXT, header.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_ISSUES, header.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table, stretch=1)

        bottom = QHBoxLayout()
        self.play_btn = self._button(bottom, "subs_play", self._play)
        self.split_btn = self._button(bottom, "subs_split", self._split)
        self.merge_btn = self._button(bottom, "subs_merge", self._merge)
        self.delete_btn = self._button(bottom, "subs_delete", self._delete)
        bottom.addStretch()
        layout.addLayout(bottom)
        self._i18n.add(self._retranslate_table)
        self._render()
        self._i18n.call(self._rerender_keeping_selection)
        self._i18n.bind()

    def _retranslate_table(self) -> None:
        self.table.setHorizontalHeaderLabels(
            [tr("subs_col_in"), tr("subs_col_out"), tr("subs_col_text"), tr("subs_col_issues")])
        self.table.setAccessibleName(tr("subs_table"))

    def _rerender_keeping_selection(self) -> None:
        """Rows carry issue texts and the status line is derived: rebuild
        them in the new language without losing the cue the user is on."""
        row, col = self.table.currentRow(), self.table.currentColumn()
        self._render()
        if 0 <= row < self.table.rowCount():
            self.table.setCurrentCell(row, max(col, 0))

    def _button(self, row, key: str, slot) -> QPushButton:
        btn = self._i18n.text(QPushButton(), key)
        btn.clicked.connect(slot)
        row.addWidget(btn)
        return btn

    # ------------------------------------------------------------------ context

    def set_provenance(self, record_id: Optional[int], source_path: Optional[str]) -> None:
        self._record_id = record_id
        self._source_path = source_path
        self._load()

    def set_result(self, result: Any) -> None:
        """The open transcript (segments, duration, revision)."""
        from domain.artifact_provenance import transcript_revision

        self._segments = list(result.segments)
        self._duration = float(getattr(result, "duration", 0.0) or 0.0)
        self._revision = transcript_revision(
            result.segments, result.language, getattr(result, "speaker_names", None))
        self._render()

    def clear(self) -> None:
        self._track = sub.SubtitleTrack()
        self._snapshot = None
        self._segments = []
        self._record_id = None
        self._unsupported = False
        self._render()
        self.content_changed.emit()

    def has_content(self) -> bool:
        return bool(self._track.cues)

    def _dir(self) -> Path:
        from core.paths import artifact_dir

        return artifact_dir(self._record_id if self._record_id is not None else "unsaved",
                            self._source_path or "recording")

    def _load(self) -> None:
        self._snapshot = None
        self._unsupported = False
        self._track = sub.SubtitleTrack()
        if isinstance(self._record_id, int):
            try:
                loaded = subtitle_store.load_track(self._dir())
            except Exception as exc:  # noqa: BLE001 - a broken file must not break the tab
                logger.warning("Could not read subtitles: %s", exc)
                loaded = sub.SubtitleTrack()
            if loaded is None:
                self._unsupported = True
            else:
                self._track = loaded
        self._render()
        self.content_changed.emit()

    def _save(self) -> None:
        if self._unsupported or not isinstance(self._record_id, int):
            return
        try:
            subtitle_store.save_track(self._dir(), self._track)
        except OSError as exc:
            logger.warning("Could not save subtitles: %s", exc)
            self.message.emit(tr("subs_save_error"), "error")

    # ------------------------------------------------------------------ render

    def issues(self) -> list[sub.Issue]:
        return sub.validate(self._track.cues, self._track.preset, self._duration or None)

    def _render(self) -> None:
        issues = self.issues()
        by_cue: dict[int, list[str]] = {}
        for issue in issues:
            label = tr(f"subs_issue_{issue.code}")
            by_cue.setdefault(issue.index, []).append(
                f"{label} ({issue.detail})" if issue.detail else label)
        self._filling = True
        self.table.setRowCount(len(self._track.cues))
        for row, cue in enumerate(self._track.cues):
            for col, value in ((_COL_TIME_IN, stamp(cue.start)), (_COL_TIME_OUT, stamp(cue.end)),
                               (_COL_TEXT, cue.text)):
                item = QTableWidgetItem(value)
                self.table.setItem(row, col, item)
            note = QTableWidgetItem("; ".join(by_cue.get(row, [])) or "✓")
            note.setFlags(note.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, _COL_ISSUES, note)
        self.table.resizeRowsToContents()
        self._filling = False
        has = bool(self._track.cues)
        for btn in (self.srt_btn, self.vtt_btn, self.play_btn, self.split_btn,
                    self.merge_btn, self.delete_btn):
            btn.setEnabled(has and not self._unsupported)
        self.generate_btn.setEnabled(bool(self._segments) and not self._unsupported)
        self.import_btn.setEnabled(not self._unsupported)
        self.undo_btn.setEnabled(self._snapshot is not None and not self._unsupported)
        self.status.setText(self._status_text(issues))

    def _status_text(self, issues: list[sub.Issue]) -> str:
        if self._unsupported:
            return tr("subs_unsupported")
        if not self._track.cues:
            return tr("subs_empty")
        parts = [tr("subs_count", count=len(self._track.cues))]
        parts.append(tr("subs_issues_count", count=len(issues)) if issues else tr("subs_no_issues"))
        if self._track.origin:
            parts.append(tr("subs_origin", name=self._track.origin))
        if self._track.transcript_revision and self._revision \
                and self._track.transcript_revision != self._revision:
            parts.append(tr("subs_stale"))
        return " · ".join(parts)

    # ------------------------------------------------------------------ edits

    def _changed(self) -> None:
        self._save()
        self._render()
        self.content_changed.emit()

    def _row(self) -> int:
        return self.table.currentRow()

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        row, col = item.row(), item.column()
        if not 0 <= row < len(self._track.cues):
            return
        cues = self._track.cues
        if col == _COL_TEXT:
            text = "\n".join(line.strip() for line in item.text().split("\n"))
            self._track.cues = sub.with_cue(cues, row, text=text)
        elif col in (_COL_TIME_IN, _COL_TIME_OUT):
            value = parse_stamp(item.text())
            if value is None:
                self.message.emit(tr("subs_bad_time"), "error")
                self._render()
                return
            self._track.cues = sub.with_cue(cues, row, **({"start": value} if col == _COL_TIME_IN
                                                          else {"end": value}))
        self._changed()

    def _generate(self) -> None:
        if self._track.cues:
            reply = QMessageBox.question(
                self, tr("subs_generate"), tr("subs_generate_confirm"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._snapshot = copy.deepcopy(self._track)
        self._track.cues = sub.cues_from_segments(self._segments, self._track.preset)
        self._track.transcript_revision = self._revision
        self._track.origin = ""
        self._changed()

    def _import(self) -> None:
        """Bring back a subtitle file edited elsewhere: read and checked
        first, shown as a summary, then replaces or joins the current track
        (one step back with Undo)."""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("subs_import"), "", "Subtitles (*.srt *.vtt);;All files (*)")
        if not path:
            return
        try:
            cues = subtitle_store.read_subtitle_file(path)
        except subtitle_store.SubtitleImportError as exc:
            reason = str(exc)
            known = {"too_large", "not_utf8", "no_cues"}
            QMessageBox.warning(self, tr("subs_import"),
                                tr(f"subs_import_{reason}") if reason in known else reason)
            return
        name = Path(path).name
        problems = sub.validate(cues, self._track.preset, self._duration or None)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("subs_import"))
        box.setText(tr("subs_import_summary", name=name, count=len(cues),
                       start=stamp(cues[0].start), end=stamp(max(c.end for c in cues)),
                       issues=len(problems)))
        if self._track.cues:
            box.setInformativeText(tr("subs_import_existing", count=len(self._track.cues)))
        replace_btn = box.addButton(tr("subs_import_replace"), QMessageBox.ButtonRole.DestructiveRole)
        add_btn = box.addButton(tr("subs_import_add"), QMessageBox.ButtonRole.AcceptRole) \
            if self._track.cues else None
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        chosen = box.clickedButton()
        if chosen is not replace_btn and (add_btn is None or chosen is not add_btn):
            return
        self._snapshot = copy.deepcopy(self._track)
        if chosen is replace_btn:
            self._track.cues = subtitle_store.combine_cues(self._track.cues, cues, "replace")
            self._track.transcript_revision = ""        # no longer made from this transcript
            self._track.origin = name
        else:
            self._track.cues = subtitle_store.combine_cues(self._track.cues, cues, "add")
            self._track.origin = ", ".join(x for x in (self._track.origin, name) if x)
        self._changed()

    def _undo(self) -> None:
        if self._snapshot is None:
            return
        self._track, self._snapshot = self._snapshot, None
        self._changed()

    def _edit_preset(self) -> None:
        dialog = _PresetDialog(self._track.preset, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._track.preset = dialog.preset()
            self._changed()

    def _split(self) -> None:
        row = self._row()
        if not 0 <= row < len(self._track.cues):
            return
        text = self._track.cues[row].text.replace("\n", " ")
        self._track.cues = sub.split_cue(self._track.cues, row, len(text) // 2)
        self._changed()

    def _merge(self) -> None:
        row = self._row()
        if not 0 <= row < len(self._track.cues) - 1:
            return
        self._track.cues = sub.merge_cues(self._track.cues, row, self._track.preset)
        self._changed()

    def _delete(self) -> None:
        row = self._row()
        if 0 <= row < len(self._track.cues):
            self._track.cues = [c for i, c in enumerate(self._track.cues) if i != row]
            self._changed()

    def _play(self) -> None:
        row = self._row()
        if 0 <= row < len(self._track.cues):
            cue = self._track.cues[row]
            self.listen_requested.emit(cue.start, cue.end, False)

    # ------------------------------------------------------------------ export

    def export_text(self, fmt: str) -> str:
        return sub.to_srt(self._track.cues) if fmt == "srt" else sub.to_vtt(self._track.cues)

    def _export(self, fmt: str) -> None:
        blocked = sub.blocking(self.issues())
        if blocked:
            first = blocked[0]
            QMessageBox.warning(self, tr("subs_export"),
                                tr("subs_export_blocked", number=first.index + 1,
                                   issue=tr(f"subs_issue_{first.code}")))
            return
        default = f"subtitles.{fmt}"
        path, _ = QFileDialog.getSaveFileName(self, tr("subs_export"), default)
        if not path:
            return
        try:
            subtitle_store.write_subtitle_file(self._track, path, fmt)
        except OSError as exc:
            QMessageBox.warning(self, tr("subs_export"), str(exc))
            return
        self.message.emit(tr("subs_exported", name=Path(path).name), "success")
