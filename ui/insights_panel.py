"""
Whispered – Insights Panel
Smart summaries: chapters, action items, key moments.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel,
    QPushButton, QFrame, QApplication, QPlainTextEdit,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

from core.insights_export import format_insight_text, item_start
from core.logger import get_logger
from core.i18n import tr
from ui.components import ChapterRow, FlowLayout
from ui.i18n_helpers import Retranslator
from core.paths import output_dir
from ui.toast import show_toast
from utils import format_duration

logger = get_logger(__name__)


class _SectionHeader(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setProperty("role", "heading")
        self.setStyleSheet("padding: 6px 0 2px 0;")


class _ActionRow(QWidget):
    """A task: its time (a link that seeks, I2 — only when the model gave
    one; nothing is made up), the task, then owner and deadline."""

    seek_requested = pyqtSignal(int)

    def __init__(
        self, task: str, owner: Optional[str], deadline: Optional[str],
        start: Optional[int] = None, parent=None,
    ):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)
        if start is not None:
            ts_btn = QPushButton(format_duration(start))
            ts_btn.setFixedWidth(48)
            ts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            ts_btn.setProperty("role", "timestamp-link")
            ts_btn.setStyleSheet("font-size: 11px;")
            ts_btn.clicked.connect(lambda: self.seek_requested.emit(start))
            layout.addWidget(ts_btn, alignment=Qt.AlignmentFlag.AlignTop)
        else:
            layout.addSpacing(56)
        parts = [f"• {task}"]
        if owner:
            parts.append(f"  {tr('insights_owner')} {owner}")
        if deadline:
            parts.append(f"  {tr('insights_deadline')} {deadline}")
        label = QLabel("\n".join(parts))
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet("font-size: 12px;")
        layout.addWidget(label, stretch=1)


_SECTIONS = ("chapters", "action_items", "key_moments")
_SECTION_KEYS = {
    "chapters": "insights_chapters",
    "action_items": "insights_action_items",
    "key_moments": "insights_key_moments",
}


class _MomentRow(QWidget):
    seek_requested = pyqtSignal(int)

    def __init__(self, start: int, quote: str, note: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(8)

        ts = format_duration(start)
        ts_btn = QPushButton(ts)
        ts_btn.setFixedWidth(48)
        ts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ts_btn.setProperty("role", "timestamp-link")
        ts_btn.setStyleSheet("font-size: 11px;")
        ts_btn.clicked.connect(lambda: self.seek_requested.emit(start))
        top.addWidget(ts_btn)

        quote_lbl = QLabel(f'"{quote}"')
        quote_lbl.setWordWrap(True)
        quote_lbl.setStyleSheet("font-size: 12px; font-style: italic;")
        top.addWidget(quote_lbl, stretch=1)
        layout.addLayout(top)

        note_lbl = QLabel(note)
        note_lbl.setWordWrap(True)
        note_lbl.setProperty("role", "muted")
        note_lbl.setStyleSheet("font-size: 11px; padding-left: 56px;")
        layout.addWidget(note_lbl)


class InsightsPanel(QWidget):
    """Insights tab — chapters, action items, key moments."""

    seek_requested = pyqtSignal(int)
    generate_requested = pyqtSignal()
    # Its own (generated) chapters may have changed — see own_chapters().
    chapters_changed = pyqtSignal()
    generation_finished = pyqtSignal(bool)
    # Whether the panel has anything to show changed (see has_content()).
    content_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments = []
        self._transcript_language: str | None = None
        # Raw (unrendered) result per generated type — needed to save to
        # disk later, since _render_*() only ever builds display widgets
        # from it and doesn't keep the data itself.
        self._results: dict[str, list] = {}
        # The record's chapters from the YouTube tab (with the user's edits);
        # shown instead of this panel's own when present (Y6).
        self._shared_chapters: list | None = None
        # Set via set_provenance()/set_source_name() by MainWindow whenever
        # the open transcript changes — recorded into each saved file's
        # Artifact manifest and used for its filename stem.
        self._record_id: int | None = None
        self._source_path: str | None = None
        self._source_name: str = ""
        self._generating = False
        self._error_message: str | None = None
        # Sections shown — and copied/saved (I1). What is *generated* is
        # the step's business, not this filter's.
        self._visible_sections: set[str] = set(_SECTIONS)
        # The user's own notes (L1/I3): loaded per saved record, saved
        # after a pause in typing.
        self._notes_loaded = ""
        self._i18n = Retranslator()
        self._setup_ui()
        self._i18n.call(self._retranslate_insights)
        self._i18n.bind()

    def _retranslate_insights(self) -> None:
        self._save_btn.setText(tr("insights_save"))
        self._copy_btn.setText(tr("btn_copy"))
        self._ch_note.setText(tr("insights_chapters_shared"))
        self._notes_header.setText(tr("insights_my_notes"))
        self._notes_edit.setPlaceholderText(tr("insights_notes_placeholder"))
        self._render_section_meta()
        self._gen_btn.setText(
            tr("insights_generating") if self._generating else tr("insights_generate")
        )
        if self._error_message is not None:
            self._placeholder.setText(f"{tr('insights_error')} {self._error_message}")
        elif not self._results:
            self._placeholder.setText(tr("insights_placeholder"))

    # ── UI ──────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(8)

        self._placeholder = QLabel(tr("insights_placeholder"))
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setProperty("role", "dim")
        self._placeholder.setStyleSheet("font-size: 13px;")
        self._placeholder.setWordWrap(True)
        outer.addWidget(self._placeholder)

        gen_row = QHBoxLayout()
        self._gen_btn = QPushButton(tr("insights_generate"))
        self._gen_btn.setProperty("variant", "primary")
        self._gen_btn.setEnabled(False)
        self._gen_btn.clicked.connect(self.generate_requested.emit)
        gen_row.addWidget(self._gen_btn)

        self._copy_btn = QPushButton(tr("btn_copy"))
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._copy_visible)
        gen_row.addWidget(self._copy_btn)

        self._save_btn = QPushButton(tr("insights_save"))
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save_to_files)
        gen_row.addWidget(self._save_btn)

        gen_row.addStretch()
        outer.addLayout(gen_row)

        # Section chips (I1): which sections are shown and copied/saved.
        self._chips = QWidget()
        self._chips.setProperty("role", "transparent")
        chips_layout = FlowLayout(self._chips, spacing=6)
        chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_buttons: dict[str, QPushButton] = {}
        for key in _SECTIONS:
            chip = QPushButton()
            chip.setCheckable(True)
            chip.setChecked(True)
            chip.setProperty("role", "quick-chip")
            chip.toggled.connect(lambda checked, k=key: self._set_section_visible(k, checked))
            chips_layout.addWidget(chip)
            self._chip_buttons[key] = chip
        self._chips.setVisible(False)
        outer.addWidget(self._chips)

        # My notes (L1/I3): the user's text, in the primary colour, above
        # everything the model wrote (shown muted below). Given to the
        # next Insights run as extra input.
        self._notes_header = _SectionHeader(tr("insights_my_notes"))
        outer.addWidget(self._notes_header)
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setProperty("role", "user-notes")
        self._notes_edit.setPlaceholderText(tr("insights_notes_placeholder"))
        self._notes_edit.setFixedHeight(96)
        self._notes_edit.textChanged.connect(self._on_notes_edited)
        outer.addWidget(self._notes_edit)
        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.setInterval(700)
        self._notes_timer.timeout.connect(self._save_notes)
        self._set_notes_enabled(False)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        self._content = QVBoxLayout(container)
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(4)

        self._ch_header = _SectionHeader(tr("insights_chapters"))
        self._content.addWidget(self._ch_header)
        self._ch_empty = self._empty_label()
        self._content.addWidget(self._ch_empty)
        self._ch_note = QLabel(tr("insights_chapters_shared"))
        self._ch_note.setWordWrap(True)
        self._ch_note.setProperty("role", "muted")
        self._ch_note.setStyleSheet("font-size: 11px;")
        self._ch_note.setVisible(False)
        self._content.addWidget(self._ch_note)
        self._ch_container = QWidget()
        self._ch_layout = QVBoxLayout(self._ch_container)
        self._ch_layout.setContentsMargins(0, 0, 0, 0)
        self._ch_layout.setSpacing(2)
        self._content.addWidget(self._ch_container)

        self._sep1 = sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setProperty("role", "divider-text")
        self._content.addWidget(sep1)

        self._ai_header = _SectionHeader(tr("insights_action_items"))
        self._content.addWidget(self._ai_header)
        self._ai_empty = self._empty_label()
        self._content.addWidget(self._ai_empty)
        self._ai_container = QWidget()
        self._ai_layout = QVBoxLayout(self._ai_container)
        self._ai_layout.setContentsMargins(0, 0, 0, 0)
        self._ai_layout.setSpacing(2)
        self._content.addWidget(self._ai_container)

        self._sep2 = sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setProperty("role", "divider-text")
        self._content.addWidget(sep2)

        self._km_header = _SectionHeader(tr("insights_key_moments"))
        self._content.addWidget(self._km_header)
        self._km_empty = self._empty_label()
        self._content.addWidget(self._km_empty)
        self._km_container = QWidget()
        self._km_layout = QVBoxLayout(self._km_container)
        self._km_layout.setContentsMargins(0, 0, 0, 0)
        self._km_layout.setSpacing(2)
        self._content.addWidget(self._km_container)

        self._content.addStretch()
        scroll.setWidget(container)
        outer.addWidget(scroll, stretch=1)
        self._render_section_meta()

    @staticmethod
    def _empty_label() -> QLabel:
        label = QLabel()
        label.setProperty("role", "dim")
        label.setStyleSheet("font-size: 12px; padding: 2px 0 6px 56px;")
        label.setVisible(False)
        return label

    # ── Sections: counts, empty notes, the show/copy filter (I1) ────

    def _section_items(self, key: str) -> list:
        if key == "chapters":
            return list(self._shown_chapters())
        data = self._results.get(key)
        return list(data) if isinstance(data, list) else []

    def _section_widgets(self, key: str) -> list:
        return {
            "chapters": [self._ch_header, self._ch_empty, self._ch_container],
            "action_items": [self._sep1, self._ai_header, self._ai_empty, self._ai_container],
            "key_moments": [self._sep2, self._km_header, self._km_empty, self._km_container],
        }[key]

    def _render_section_meta(self) -> None:
        """Headers with counts, "nothing found" notes, chip captions and
        which sections are on screen — from the current results."""
        has_results = bool(self._results) or bool(self._shared_chapters)
        headers = {"chapters": self._ch_header, "action_items": self._ai_header,
                   "key_moments": self._km_header}
        empties = {"chapters": self._ch_empty, "action_items": self._ai_empty,
                   "key_moments": self._km_empty}
        for key in _SECTIONS:
            count = len(self._section_items(key))
            title = tr(_SECTION_KEYS[key])
            # A section exists once its step produced it — chapters may
            # also come from the YouTube tab without Insights being run.
            generated = key in self._results or (key == "chapters" and bool(self._shared_chapters))
            headers[key].setText(f"{title} · {count}" if generated else title)
            empties[key].setText(tr(f"insights_none_{key}"))
            self._chip_buttons[key].setText(f"{title} · {count}")
            self._chip_buttons[key].setVisible(generated)
            shown = generated and key in self._visible_sections
            for widget in self._section_widgets(key):
                widget.setVisible(shown)
            empties[key].setVisible(shown and count == 0)
        self._ch_note.setVisible(
            "chapters" in self._visible_sections and bool(self._shared_chapters)
        )
        self._chips.setVisible(has_results)
        self._copy_btn.setEnabled(has_results)
        # Once there are results, running again is the secondary action.
        if not self._generating:
            self._gen_btn.setText(
                tr("insights_regenerate") if self._results else tr("insights_generate")
            )
        variant = "" if self._results else "primary"
        if self._gen_btn.property("variant") != variant:
            self._gen_btn.setProperty("variant", variant)
            self._gen_btn.style().unpolish(self._gen_btn)
            self._gen_btn.style().polish(self._gen_btn)

    def _set_section_visible(self, key: str, visible: bool) -> None:
        if visible:
            self._visible_sections.add(key)
        else:
            self._visible_sections.discard(key)
        self._render_section_meta()

    def _visible_results(self) -> dict:
        """What is on screen, section by section — what Copy and Save use."""
        results = dict(self._results)
        if self._shared_chapters:
            results["chapters"] = self._shared_chapters
        return {k: v for k, v in results.items() if k in self._visible_sections}

    def _copy_visible(self) -> None:
        blocks = []
        for key in _SECTIONS:
            data = self._visible_results().get(key)
            text = format_insight_text(key, data) if isinstance(data, list) else ""
            if text:
                blocks.append(f"{tr(_SECTION_KEYS[key])}\n{text}")
        if not blocks:
            show_toast(self, tr("insights_nothing_to_save"), kind="error")
            return
        QApplication.clipboard().setText("\n\n".join(blocks))
        show_toast(self, tr("toast_copied"), kind="success")

    # ── Public API ──────────────────────────────────────────────────

    def set_segments(self, segments, transcript_language: str | None = None) -> None:
        self._segments = segments
        self._transcript_language = transcript_language
        self._gen_btn.setEnabled(bool(segments))
        if segments:
            self._placeholder.hide()
        else:
            self._placeholder.show()

    def set_provenance(self, record_id: int | None, source_path: str | None) -> None:
        """Called by MainWindow whenever the open transcript's identity
        changes — recorded into each saved file's Artifact manifest, and
        where the record's notes live."""
        self._flush_notes()
        self._record_id = record_id
        self._source_path = source_path
        self.reload_notes()

    # ── My notes (L1/I3) ────────────────────────────────────────────

    def _notes_folder(self):
        from core.paths import artifact_dir

        return artifact_dir(self._record_id, self._source_path or "recording")

    def _set_notes_enabled(self, enabled: bool) -> None:
        self._notes_edit.setEnabled(enabled)
        self._notes_header.setVisible(enabled)
        self._notes_edit.setVisible(enabled)

    def reload_notes(self) -> None:
        """Show the open record's notes (notes need a saved record)."""
        from application.user_notes import load_notes

        saved = isinstance(self._record_id, int)
        text = load_notes(self._notes_folder()) if saved else ""
        self._notes_loaded = text
        self._notes_edit.blockSignals(True)
        self._notes_edit.setPlainText(text)
        self._notes_edit.blockSignals(False)
        self._set_notes_enabled(saved)
        self.content_changed.emit()

    def notes(self) -> str:
        return self._notes_edit.toPlainText()

    def _on_notes_edited(self) -> None:
        self._notes_timer.start()

    def _flush_notes(self) -> None:
        if self._notes_timer.isActive():
            self._notes_timer.stop()
            self._save_notes()

    def _save_notes(self) -> None:
        if not isinstance(self._record_id, int):
            return
        text = self._notes_edit.toPlainText()
        if text == self._notes_loaded:
            return
        from application.user_notes import save_notes

        try:
            save_notes(self._notes_folder(), text, self._record_id)
            self._notes_loaded = text
        except OSError as exc:
            logger.warning("Failed to save notes: %s", exc)
            show_toast(self, tr("insights_notes_save_error"), kind="error")
        self.content_changed.emit()

    def set_source_name(self, name: str) -> None:
        """Base filename (no extension) used when saving generated files."""
        self._source_name = name or ""

    def shutdown(self) -> None:
        """Part of the Shutdownable protocol (ui/shutdownable.py). This
        panel no longer owns any worker — the "insights" JobRunner it
        triggers via generate_requested lives on MainWindow now (see
        docs/UI_REDESIGN_PLAN_2026-09.ru.md, B5c) and is shut down there,
        the same way _clean_job/_article_job already are."""
        self.clear()

    def clear(self) -> None:
        self._flush_notes()
        self._segments = []
        self._transcript_language = None
        self._generating = False
        self._error_message = None
        self._gen_btn.setEnabled(False)
        self._gen_btn.setText(tr("insights_generate"))
        self._save_btn.setEnabled(False)
        self._results.clear()
        for layout in (self._ai_layout, self._km_layout):
            self._clear_section(layout)
        self._render_chapter_section()
        self.chapters_changed.emit()
        self._placeholder.setText(tr("insights_placeholder"))
        self._placeholder.show()
        self._render_section_meta()
        self.content_changed.emit()

    def has_content(self) -> bool:
        """Results, a generation in progress, a failure to report, or the
        user's own notes."""
        return (
            bool(self._results) or self._generating or self._error_message is not None
            or bool(self._notes_loaded.strip())
        )

    # ── Generation ──────────────────────────────────────────────────
    # This panel no longer runs anything itself — generate_requested asks
    # MainWindow to run the "insights" step via JobRunner (application/
    # steps.py), and begin_generating()/set_result()/set_error() below are
    # its side of that: busy-state before the job starts, and the two ways
    # it can end. Kept as three separate calls (rather than one signal
    # payload) so MainWindow's own direct calls to begin_generating()
    # read the same as a real button click — see
    # MainWindow._start_insights_job().

    def begin_generating(self) -> None:
        self._generating = True
        self._error_message = None
        self._gen_btn.setEnabled(False)
        self._gen_btn.setText(tr("insights_generating"))
        self._placeholder.hide()
        self._save_btn.setEnabled(False)
        self.content_changed.emit()

    def set_result(self, payload: dict) -> None:
        """*payload* is application/steps.py's "insights" step output:
        ``{"chapters": [...], "action_items": [...], "key_moments": [...]}``."""
        self._results = dict(payload)
        self._generating = False
        self._error_message = None
        self._gen_btn.setEnabled(True)
        self._gen_btn.setText(tr("insights_generate"))
        self._save_btn.setEnabled(bool(self._results))
        self._render_chapter_section()
        self._clear_section(self._ai_layout)
        self._render_action_items(list(payload.get("action_items") or []))
        self._clear_section(self._km_layout)
        self._render_key_moments(list(payload.get("key_moments") or []))
        self._render_section_meta()
        self.chapters_changed.emit()
        self.content_changed.emit()
        self.generation_finished.emit(True)

    def set_error(self, message: str) -> None:
        self._generating = False
        self._error_message = message
        self._gen_btn.setEnabled(True)
        self._gen_btn.setText(tr("insights_generate"))
        self._placeholder.setText(f"{tr('insights_error')} {message}")
        self._placeholder.show()
        self.content_changed.emit()
        self.generation_finished.emit(False)

    # ── Chapters shared with the YouTube tab (Y6) ───────────────────

    def own_chapters(self) -> list[dict]:
        """The chapters this panel's own insights step produced."""
        data = self._results.get("chapters")
        return list(data) if isinstance(data, list) else []

    def set_shared_chapters(self, chapters: list | None) -> None:
        """Show *chapters* (the record's, from the YouTube tab) instead of
        this panel's own; ``None`` or empty goes back to its own."""
        shared = list(chapters) if chapters else None
        if shared == self._shared_chapters:
            return
        self._shared_chapters = shared
        self._render_chapter_section()

    def _shown_chapters(self) -> list:
        return self._shared_chapters if self._shared_chapters else self.own_chapters()

    def _render_chapter_section(self) -> None:
        self._clear_section(self._ch_layout)
        self._render_chapters(self._shown_chapters())
        self._ch_note.setVisible(bool(self._shared_chapters))
        if hasattr(self, "_chip_buttons"):
            self._render_section_meta()

    # ── Export ──────────────────────────────────────────────────────

    def _save_to_files(self) -> None:
        """Save every generated section to output/ in the app data dir —
        same location and one-file-per-section shape as
        ui/youtube_panel.py's save button, adapted for Insights' three
        sections all being visible at once rather than one tab at a time."""
        if not self._results:
            show_toast(self, tr("insights_nothing_to_save"), kind="error")
            return

        directory = output_dir()
        stem = self._source_name or "insights"
        saved = 0
        # Save what is on screen: the record's shared chapters when shown,
        # and only the sections the chips leave visible.
        results = self._visible_results()
        for insight_type, data in results.items():
            text = format_insight_text(insight_type, data)
            if not text:
                continue
            try:
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / f"{stem}_{insight_type}.txt"
                path.write_text(text, encoding="utf-8")
                saved += 1
                self._write_provenance(path, insight_type)
            except OSError as exc:
                logger.warning("Failed to save %s to %s: %s", insight_type, directory, exc)
                show_toast(
                    self, tr("insights_save_error", section=insight_type), kind="error"
                )

        if saved:
            show_toast(self, tr("insights_saved_files", count=saved), kind="success")

    def _write_provenance(self, path, insight_type: str) -> None:
        """Best-effort Artifact manifest write (see
        docs/archive/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R5-full step 3) — same
        mechanism already used for Cover/article/YouTube/book exports. The
        .txt file is already safely on disk by the time this runs, so a
        manifest failure must not turn a successful save into an error."""
        from application.artifacts import record_export

        record_export(
            record_id=self._record_id,
            source_path=self._source_path,
            segments=self._segments,
            language=self._transcript_language,
            type=f"insights_{insight_type}",
            path=path,
        )

    # ── Renderers ───────────────────────────────────────────────────

    def _render_chapters(self, data: list):
        for item in data:
            try:
                start = int(item.get("start", 0))
                title = str(item.get("title", ""))
                if not title:
                    continue
                row = ChapterRow(start, title, self._ch_container)
                row.seek_requested.connect(self.seek_requested)
                self._ch_layout.addWidget(row)
            except Exception:
                pass

    def _render_action_items(self, data: list):
        for item in data:
            try:
                task = str(item.get("task", ""))
                if not task:
                    continue
                row = _ActionRow(
                    task, item.get("owner"), item.get("deadline"), item_start(item),
                    self._ai_container,
                )
                row.seek_requested.connect(self.seek_requested)
                self._ai_layout.addWidget(row)
            except Exception:
                pass

    def _render_key_moments(self, data: list):
        for item in data:
            try:
                start = int(item.get("start", 0))
                quote = str(item.get("quote", ""))
                note = str(item.get("note", ""))
                if not quote:
                    continue
                row = _MomentRow(start, quote, note, self._km_container)
                row.seek_requested.connect(self.seek_requested)
                self._km_layout.addWidget(row)
            except Exception:
                pass

    @staticmethod
    def _clear_section(layout: QVBoxLayout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                # Detach now: deleteLater() alone leaves the old row in the
                # tree (and in findChildren) until the event loop runs —
                # sections are now re-rendered while staying on screen.
                widget.setParent(None)
                widget.deleteLater()
