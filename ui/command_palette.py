"""Ctrl+K command palette for records and existing application actions."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from core.i18n import tr


_ACTIONS = (
    ("new", "command_new"),
    ("youtube", "command_youtube"),
    ("export", "command_export"),
    ("live", "command_live"),
    ("queue", "command_queue"),
    ("settings", "command_settings"),
)


class CommandPalette(QDialog):
    """Keyboard-first search overlay backed by the existing history FTS."""

    record_requested = pyqtSignal(int)
    action_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("command_palette_title"))
        self.setModal(True)
        self.resize(620, 420)
        layout = QVBoxLayout(self)
        title = QLabel(tr("command_palette_title"))
        title.setProperty("role", "page-title")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("command_palette_placeholder"))
        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self._activate_current)
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.itemActivated.connect(self._activate)
        layout.addWidget(self.results, stretch=1)

    def open_palette(self) -> None:
        self.search.clear()
        self._refresh("")
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()

    def _refresh(self, query: str) -> None:
        self.results.clear()
        needle = query.strip().casefold()
        for key, label_key in _ACTIONS:
            label = tr(label_key)
            if not needle or needle in label.casefold():
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, ("action", key))
                self.results.addItem(item)
        try:
            from core.history import get_history_store

            records = get_history_store().search(query) if query.strip() else get_history_store().list(limit=12)
            for record in records[:20]:
                item = QListWidgetItem(tr("command_record", name=record.source_name))
                item.setData(Qt.ItemDataRole.UserRole, ("record", record.id))
                self.results.addItem(item)
        except Exception:
            pass
        if self.results.count():
            self.results.setCurrentRow(0)

    def _activate_current(self) -> None:
        item = self.results.currentItem()
        if item is not None:
            self._activate(item)

    def _activate(self, item: QListWidgetItem) -> None:
        kind, value = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        if kind == "record":
            self.record_requested.emit(int(value))
        else:
            self.action_requested.emit(str(value))
