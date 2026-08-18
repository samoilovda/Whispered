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

from core.logger import get_logger
from core.i18n import tr
from core.worker_registry import WorkerRegistry
from utils import format_duration, language_name_for_code

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
    generation_finished = pyqtSignal(bool)

    def __init__(self, parent=None, insights_cache=None):
        super().__init__(parent)
        self._segments = []
        self._transcript_language: str | None = None
        self._workers: dict[str, object] = {}
        self._registry = WorkerRegistry(parent=self)
        # Shared with YouTubePanel by MainWindow so a type both panels
        # generate (e.g. "chapters") isn't computed twice — see
        # core/insights_cache.py.
        self._insights_cache = insights_cache
        self._pending = 0
        self._insight_queue: list[str] = []
        self._any_error = False
        self._setup_ui()

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
        self._gen_btn.clicked.connect(self._generate_all)
        gen_row.addWidget(self._gen_btn)
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

    def shutdown(self) -> None:
        """Part of the Shutdownable protocol (ui/shutdownable.py). clear()
        already cancels in-flight workers with a bounded timeout, which is
        exactly what window close needs too."""
        self.clear()

    def clear(self) -> None:
        self._segments = []
        self._transcript_language = None
        self._gen_btn.setEnabled(False)
        for layout in (self._ch_layout, self._ai_layout, self._km_layout):
            self._clear_section(layout)
        self._cancel_workers(timeout=2000)
        self._pending = 0
        self._insight_queue.clear()
        self._placeholder.setText(tr("insights_placeholder"))
        self._placeholder.show()

    def _cancel_workers(self, timeout: int) -> None:
        """Cancel workers via WorkerRegistry and retain any that outlive the
        bounded wait until their QThread actually finishes."""
        self._workers.clear()
        self._registry.shutdown_all(timeout_ms=timeout)

    # ── Generation ──────────────────────────────────────────────────

    def _generate_all(self):
        from config import get_config
        cfg = get_config()
        if not cfg.lm_studio_url:
            self._placeholder.setText(tr("insights_no_lm"))
            self._placeholder.show()
            self.generation_finished.emit(False)
            return

        # Normally the prior run is done when the button is re-enabled, but
        # programmatic callers can start a replacement run sooner.  Reuse the
        # safe bounded-cancellation path instead of deleting blindly.
        self._cancel_workers(timeout=1000)

        self._gen_btn.setEnabled(False)
        self._gen_btn.setText(tr("insights_generating"))
        self._placeholder.hide()
        self._pending = 3
        self._any_error = False

        self._insight_queue = ["chapters", "action_items", "key_moments"]
        self._start_next_worker()

    def _start_next_worker(self) -> None:
        if not self._insight_queue:
            return
        from config import get_config
        from core.insights_worker import InsightsWorker

        insight_type = self._insight_queue.pop(0)
        lang = language_name_for_code(self._transcript_language)
        worker = InsightsWorker(
            insight_type,
            self._segments,
            get_config().lm_studio_url,
            language=lang,
            parent=self,
            cache=self._insights_cache,
        )
        worker.finished.connect(self._on_finished)
        worker.error_occurred.connect(self._on_error)
        self._workers[insight_type] = worker
        self._registry.register(worker, name=f"insights_{insight_type}")
        worker.start()

    def _decrement_pending(self):
        self._pending -= 1
        if self._pending <= 0:
            self._pending = 0
            self._gen_btn.setEnabled(True)
            self._gen_btn.setText(tr("insights_generate"))
            self.generation_finished.emit(not self._any_error)
        else:
            self._start_next_worker()

    def _on_finished(self, insight_type: str, data):
        if self._workers.get(insight_type) is not self.sender():
            return
        layout_map = {
            "chapters": self._ch_layout,
            "action_items": self._ai_layout,
            "key_moments": self._km_layout,
        }
        layout = layout_map.get(insight_type)
        if layout is not None:
            self._clear_section(layout)
            if isinstance(data, list):
                if insight_type == "chapters":
                    self._render_chapters(data)
                elif insight_type == "action_items":
                    self._render_action_items(data)
                elif insight_type == "key_moments":
                    self._render_key_moments(data)
            else:
                lbl = QLabel(str(data)[:500])
                lbl.setWordWrap(True)
                lbl.setProperty("role", "muted")
                lbl.setStyleSheet("font-size: 11px;")
                layout.addWidget(lbl)
        self._decrement_pending()

    def _on_error(self, insight_type: str, msg: str):
        if self._workers.get(insight_type) is not self.sender():
            return
        self._any_error = True
        layout_map = {
            "chapters": self._ch_layout,
            "action_items": self._ai_layout,
            "key_moments": self._km_layout,
        }
        layout = layout_map.get(insight_type)
        if layout is not None:
            self._clear_section(layout)
            lbl = QLabel(f"{tr('insights_error')} {msg}")
            lbl.setWordWrap(True)
            lbl.setProperty("role", "danger-text")
            lbl.setStyleSheet("font-size: 11px;")
            layout.addWidget(lbl)
        self._decrement_pending()

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
