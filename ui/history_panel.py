"""
Whispered – History Panel
Browse, open and delete past transcription results.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QLineEdit, QMenu, QMessageBox, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import QFont

from core.logger import get_logger

logger = get_logger(__name__)


def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.rstrip("Z"))
        return dt.strftime("%d %b %Y, %H:%M")
    except Exception:
        return iso


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
        self._search_edit.setPlaceholderText("Search history…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_search)
        toolbar.addWidget(self._search_edit, stretch=1)

        self._refresh_btn = QPushButton("↺")
        self._refresh_btn.setFixedWidth(28)
        self._refresh_btn.setToolTip("Refresh list")
        self._refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self._refresh_btn)

        self._clear_btn = QPushButton("Clear All")
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
        for rec in self._records:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, rec.id)

            name = rec.source_name or rec.source_path
            date = _fmt_date(rec.created_at)
            dur = _fmt_duration(rec.duration)
            lang = rec.language.upper() if rec.language else "?"

            item.setText(f"{name}\n{date}  ·  {dur}  ·  {lang}")
            item.setFont(QFont("Sans", 10))
            self._list.addItem(item)

        total = len(self._records)
        self._status.setText(f"{total} record{'s' if total != 1 else ''}")

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
        open_act = menu.addAction("Open")
        menu.addSeparator()
        delete_act = menu.addAction("Delete")

        action = menu.exec(self._list.mapToGlobal(pos))
        if action == open_act:
            self.open_record.emit(record_id)
        elif action == delete_act:
            self._delete_record(record_id)

    def _delete_record(self, record_id: int):
        reply = QMessageBox.question(
            self, "Delete",
            "Delete this history entry? The original file is not affected.",
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
            self, "Clear History",
            "Delete ALL history entries? This cannot be undone.",
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
