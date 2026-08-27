"""
Whispered – Insights Panel
Smart summaries: chapters, action items, key moments.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel,
    QPushButton, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal

from core.insights_export import format_insight_text
from core.logger import get_logger
from core.i18n import tr
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


class _ChapterRow(QWidget):
    seek_requested = pyqtSignal(int)

    def __init__(self, start: int, title: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(8)

        ts = format_duration(start)
        ts_btn = QPushButton(ts)
        ts_btn.setFixedWidth(48)
        ts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ts_btn.setProperty("role", "timestamp-link")
        ts_btn.setStyleSheet("font-size: 11px;")
        ts_btn.clicked.connect(lambda: self.seek_requested.emit(start))
        layout.addWidget(ts_btn)

        title_lbl = QLabel(title)
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet("font-size: 12px;")
        layout.addWidget(title_lbl, stretch=1)


class _ActionRow(QLabel):
    def __init__(self, task: str, owner: Optional[str], deadline: Optional[str], parent=None):
        parts = [f"• {task}"]
        if owner:
            parts.append(f"  {tr('insights_owner')} {owner}")
        if deadline:
            parts.append(f"  {tr('insights_deadline')} {deadline}")
        super().__init__("\n".join(parts), parent)
        self.setWordWrap(True)
        self.setStyleSheet("font-size: 12px; padding: 2px 0;")


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
    generation_finished = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments = []
        self._transcript_language: str | None = None
        # Raw (unrendered) result per generated type — needed to save to
        # disk later, since _render_*() only ever builds display widgets
        # from it and doesn't keep the data itself.
        self._results: dict[str, list] = {}
        # Set via set_provenance()/set_source_name() by MainWindow whenever
        # the open transcript changes — recorded into each saved file's
        # Artifact manifest and used for its filename stem.
        self._record_id: int | None = None
        self._source_path: str | None = None
        self._source_name: str = ""
        self._generating = False
        self._error_message: str | None = None
        self._i18n = Retranslator()
        self._setup_ui()
        self._i18n.call(self._retranslate_insights)
        self._i18n.bind()

    def _retranslate_insights(self) -> None:
        self._save_btn.setText(tr("insights_save"))
        self._ch_header.setText(tr("insights_chapters"))
        self._ai_header.setText(tr("insights_action_items"))
        self._km_header.setText(tr("insights_key_moments"))
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

        self._save_btn = QPushButton(tr("insights_save"))
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save_to_files)
        gen_row.addWidget(self._save_btn)

        gen_row.addStretch()
        outer.addLayout(gen_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        self._content = QVBoxLayout(container)
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(4)

        self._ch_header = _SectionHeader(tr("insights_chapters"))
        self._content.addWidget(self._ch_header)
        self._ch_container = QWidget()
        self._ch_layout = QVBoxLayout(self._ch_container)
        self._ch_layout.setContentsMargins(0, 0, 0, 0)
        self._ch_layout.setSpacing(2)
        self._content.addWidget(self._ch_container)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setProperty("role", "divider-text")
        self._content.addWidget(sep1)

        self._ai_header = _SectionHeader(tr("insights_action_items"))
        self._content.addWidget(self._ai_header)
        self._ai_container = QWidget()
        self._ai_layout = QVBoxLayout(self._ai_container)
        self._ai_layout.setContentsMargins(0, 0, 0, 0)
        self._ai_layout.setSpacing(2)
        self._content.addWidget(self._ai_container)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setProperty("role", "divider-text")
        self._content.addWidget(sep2)

        self._km_header = _SectionHeader(tr("insights_key_moments"))
        self._content.addWidget(self._km_header)
        self._km_container = QWidget()
        self._km_layout = QVBoxLayout(self._km_container)
        self._km_layout.setContentsMargins(0, 0, 0, 0)
        self._km_layout.setSpacing(2)
        self._content.addWidget(self._km_container)

        self._content.addStretch()
        scroll.setWidget(container)
        outer.addWidget(scroll, stretch=1)

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
        changes — recorded into each saved file's Artifact manifest."""
        self._record_id = record_id
        self._source_path = source_path

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
        self._segments = []
        self._transcript_language = None
        self._generating = False
        self._error_message = None
        self._gen_btn.setEnabled(False)
        self._gen_btn.setText(tr("insights_generate"))
        self._save_btn.setEnabled(False)
        self._results.clear()
        for layout in (self._ch_layout, self._ai_layout, self._km_layout):
            self._clear_section(layout)
        self._placeholder.setText(tr("insights_placeholder"))
        self._placeholder.show()

    # ── Generation ──────────────────────────────────────────────────
    # This panel no longer runs anything itself — generate_requested asks
    # MainWindow to run the "insights" step via JobRunner (application/
    # steps.py), and begin_generating()/set_result()/set_error() below are
    # its side of that: busy-state before the job starts, and the two ways
    # it can end. Kept as three separate calls (rather than one signal
    # payload) so the preset chain's own direct calls to begin_generating()
    # read the same as a real button click — see
    # MainWindow._start_next_extra_chain_step().

    def begin_generating(self) -> None:
        self._generating = True
        self._error_message = None
        self._gen_btn.setEnabled(False)
        self._gen_btn.setText(tr("insights_generating"))
        self._placeholder.hide()
        self._save_btn.setEnabled(False)

    def set_result(self, payload: dict) -> None:
        """*payload* is application/steps.py's "insights" step output:
        ``{"chapters": [...], "action_items": [...], "key_moments": [...]}``."""
        self._results = dict(payload)
        self._generating = False
        self._error_message = None
        self._gen_btn.setEnabled(True)
        self._gen_btn.setText(tr("insights_generate"))
        self._save_btn.setEnabled(bool(self._results))
        self._clear_section(self._ch_layout)
        self._render_chapters(list(payload.get("chapters") or []))
        self._clear_section(self._ai_layout)
        self._render_action_items(list(payload.get("action_items") or []))
        self._clear_section(self._km_layout)
        self._render_key_moments(list(payload.get("key_moments") or []))
        self.generation_finished.emit(True)

    def set_error(self, message: str) -> None:
        self._generating = False
        self._error_message = message
        self._gen_btn.setEnabled(True)
        self._gen_btn.setText(tr("insights_generate"))
        self._placeholder.setText(f"{tr('insights_error')} {message}")
        self._placeholder.show()
        self.generation_finished.emit(False)

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
        for insight_type, data in self._results.items():
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
        docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R5-full step 3) — same
        mechanism already used for Cover/article/YouTube/book exports. The
        .txt file is already safely on disk by the time this runs, so a
        manifest failure must not turn a successful save into an error."""
        try:
            from application.artifact_provenance import source_fingerprint, transcript_revision
            from domain.artifact import Artifact
            from infrastructure.persistence import artifact_store

            artifact_store.save(Artifact(
                record_id=str(self._record_id) if self._record_id is not None else "unsaved",
                source_hash=source_fingerprint(self._source_path),
                source_path=self._source_path or "",
                transcript_revision=transcript_revision(self._segments, self._transcript_language or ""),
                type=f"insights_{insight_type}",
                path=str(path),
            ))
        except Exception as exc:
            logger.warning("Failed to write insights artifact manifest for %s: %s", path, exc)

    # ── Renderers ───────────────────────────────────────────────────

    def _render_chapters(self, data: list):
        for item in data:
            try:
                start = int(item.get("start", 0))
                title = str(item.get("title", ""))
                if not title:
                    continue
                row = _ChapterRow(start, title, self._ch_container)
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
                row = _ActionRow(task, item.get("owner"), item.get("deadline"), self._ai_container)
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
            if item.widget():
                item.widget().deleteLater()
