"""Ctrl+K command palette for records, recipes, run steps and existing
application actions (B8, docs/UI_REDESIGN_PLAN_2026-09.ru.md): "the
answer to 'many features, little space'" — rare functions live here
instead of another chip or button."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from core.i18n import tr
from domain.recipe import BUILTIN_RECIPES


class CommandPalette(QDialog):
    """Keyboard-first search overlay backed by the existing history FTS.

    Generic actions (as opposed to records/recipes/retriable steps) come
    from bind_actions() — the same QAction objects MainWindow._init_menu_bar
    (B12, docs/IMPROVEMENT_PLAN_2026-08.ru.md) put in the menu bar, marked
    there as palette-eligible. This replaced a hardcoded _ACTIONS table
    plus a string-keyed action_requested signal MainWindow decoded through
    its own _run_palette_action handler dict — two lists of the same
    actions that only stayed in sync by hand. A palette row now shows
    exactly action.text() and activating it calls action.trigger(): the
    same QAction, so it can't diverge from what the menu bar does.
    """

    record_requested = pyqtSignal(int)
    recipe_requested = pyqtSignal(str)
    retry_step_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._run_view = None
        self._actions: list = []
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

    def bind_actions(self, actions) -> None:
        """QAction objects to list as generic commands (B12) — a plain
        reference to MainWindow's own list, so a menu item's enabled
        state at query time is always read live, not a snapshot from
        whenever the palette was constructed."""
        self._actions = list(actions)

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
        for action in self._actions:
            label = action.text()
            if not needle or needle in label.casefold():
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, ("action", action))
                # Shown, not hidden — a user should see the function
                # exists even when it isn't applicable right now (see
                # docs/IMPROVEMENT_PLAN_2026-08.ru.md, B12 item 4).
                if not action.isEnabled():
                    item.setFlags(
                        item.flags()
                        & ~Qt.ItemFlag.ItemIsEnabled
                        & ~Qt.ItemFlag.ItemIsSelectable
                    )
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
        if kind == "action" and not value.isEnabled():
            return
        self.accept()
        if kind == "record":
            self.record_requested.emit(int(value))
        elif kind == "recipe":
            self.recipe_requested.emit(str(value))
        elif kind == "retry_step":
            self.retry_step_requested.emit(str(value))
        else:
            value.trigger()
