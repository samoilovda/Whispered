"""Ctrl+K command palette for records, recipes, run steps and existing
application actions (B8, docs/UI_REDESIGN_PLAN_2026-09.ru.md): "the
answer to 'many features, little space'" — rare functions live here
instead of another chip or button."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from core.i18n import tr
from domain.recipe import BUILTIN_RECIPES


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
    recipe_requested = pyqtSignal(str)
    retry_step_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._run_view = None
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

    def bind_run_view(self, run_view) -> None:
        """*run_view* is asked for its currently retriable steps (B8) each
        time the palette refreshes — a plain reference, not a copy, so it
        always reflects whatever run is bound at query time."""
        self._run_view = run_view

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
        for recipe in BUILTIN_RECIPES:
            label = tr("command_run_recipe", name=tr(f"recipe_{recipe.builtin_key}"))
            if not needle or needle in label.casefold():
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, ("recipe", recipe.builtin_key))
                self.results.addItem(item)
        if self._run_view is not None:
            for name, step_label in self._run_view.retriable_steps():
                label = tr("command_retry_step", name=step_label)
                if not needle or needle in label.casefold():
                    item = QListWidgetItem(label)
                    item.setData(Qt.ItemDataRole.UserRole, ("retry_step", name))
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
        elif kind == "recipe":
            self.recipe_requested.emit(str(value))
        elif kind == "retry_step":
            self.retry_step_requested.emit(str(value))
        else:
            self.action_requested.emit(str(value))
