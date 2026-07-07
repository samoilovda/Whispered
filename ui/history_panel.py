"""
Whispered – History Panel
Browse, open and delete past transcription results.
"""

from __future__ import annotations

import re
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QLineEdit, QMenu, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import QFont

from core.logger import get_logger
from core.i18n import tr
from utils import format_duration

logger = get_logger(__name__)


_fmt_duration = format_duration


def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.rstrip("Z"))
        return dt.strftime("%d %b %Y, %H:%M")
    except Exception:
        return iso


_JSON_KEY_RE = re.compile(r'"[^"]+"\s*:\s*')


def _clean_snippet(raw: str) -> str:
    """Strip JSON structure from an FTS5 snippet to produce readable text."""
    # Remove JSON key-value punctuation like "text": "
    text = _JSON_KEY_RE.sub("", raw)
    # Remove leftover JSON delimiters
    text = re.sub(r'[\[{}\],"]', " ", text)
    # Collapse whitespace
    text = " ".join(text.split())
    return text


class HistoryPanel(QWidget):
    """
    History tab widget.
    Emits open_record(record_id) so MainWindow can load it.
    """

    open_record = pyqtSignal(int)  # record id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = None   # lazy: avoid import at startup if history_enabled=False
        self._records: list = []
        self._setup_ui()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)

        # ── Toolbar ──────────────────────────────────────────────
        toolbar = QHBoxLayout()

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr("history_search_placeholder"))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_search)
        toolbar.addWidget(self._search_edit, stretch=1)

        self._refresh_btn = QPushButton("↺")
        self._refresh_btn.setFixedWidth(28)
        self._refresh_btn.setToolTip("Refresh list")
        self._refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self._refresh_btn)

        self._clear_btn = QPushButton(tr("history_clear_all"))
        self._clear_btn.setProperty("variant", "danger")
        self._clear_btn.clicked.connect(self._clear_all)
        toolbar.addWidget(self._clear_btn)

        layout.addLayout(toolbar)

        # ── List ─────────────────────────────────────────────────
        self._list = QListWidget()
        self._list.setSpacing(2)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        self._list.itemDoubleClicked.connect(self._open_selected)
        layout.addWidget(self._list, stretch=1)

        # ── Status bar ───────────────────────────────────────────
        self._status = QLabel()
        self._status.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._status)

    # ------------------------------------------------------------------ public API

    def refresh(self):
        """Reload list from DB (called after a new transcription is saved)."""
        query = self._search_edit.text().strip()
        self._load(query)

    # ------------------------------------------------------------------ internals

    def _get_store(self):
        if self._store is None:
            from core.history import get_history_store
            self._store = get_history_store()
        return self._store

    def _load(self, query: str = ""):
        store = self._get_store()
        try:
            if query:
                self._records = store.search(query)
            else:
                self._records = store.list()
        except Exception as e:
            logger.warning("History load failed: %s", e)
            self._records = []
        self._populate()

    def _populate(self):
        self._list.clear()
        is_search = bool(self._search_edit.text().strip())
        for rec in self._records:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, rec.id)

            name = rec.source_name or rec.source_path
            date = _fmt_date(rec.created_at)
            dur = _fmt_duration(rec.duration)
            lang = rec.language.upper() if rec.language else "?"

            meta = f"{date}  ·  {dur}  ·  {lang}"
            if is_search and rec.preview:
                snippet = _clean_snippet(rec.preview)
                if snippet:
                    item.setText(f"{name}\n{meta}\n{snippet}")
                else:
                    item.setText(f"{name}\n{meta}")
            else:
                item.setText(f"{name}\n{meta}")
            item.setFont(QFont("Sans", 10))
            self._list.addItem(item)

        total = len(self._records)
        key = "history_status_plural" if total != 1 else "history_status"
        self._status.setText(tr(key, count=total))

    def _on_search(self, text: str):
        self._load(text.strip())

    def _open_selected(self, item: QListWidgetItem):
        record_id = item.data(Qt.ItemDataRole.UserRole)
        self.open_record.emit(record_id)

    def _show_context_menu(self, pos: QPoint):
        item = self._list.itemAt(pos)
        if not item:
            return
        record_id = item.data(Qt.ItemDataRole.UserRole)

        menu = QMenu(self)
        open_act = menu.addAction(tr("btn_open"))
        menu.addSeparator()
        delete_act = menu.addAction(tr("history_delete_title"))

        action = menu.exec(self._list.mapToGlobal(pos))
        if action == open_act:
            self.open_record.emit(record_id)
        elif action == delete_act:
            self._delete_record(record_id)

    def _delete_record(self, record_id: int):
        reply = QMessageBox.question(
            self,
            tr("history_delete_title"),
            tr("history_delete_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._get_store().delete(record_id)
        except Exception as e:
            logger.warning("History delete failed: %s", e)
        self.refresh()

    def _clear_all(self):
        reply = QMessageBox.question(
            self,
            tr("history_clear_title"),
            tr("history_clear_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            n = self._get_store().clear()
            logger.info("History cleared: %d records deleted", n)
        except Exception as e:
            logger.warning("History clear failed: %s", e)
        self.refresh()
