"""Ctrl+K command palette for records, recipes, run steps and existing
application actions (B8, docs/UI_REDESIGN_PLAN_2026-09.ru.md): "the
answer to 'many features, little space'" — rare functions live here
instead of another chip or button.

Rows are grouped under headers (recent records first when nothing is
typed; actions first once something is), matched forgivingly
(core/fuzzy.py: word starts, initials, letters in order) and ranked
within their group. An action's keyboard shortcut is shown on its row,
so the palette also teaches the keys. Up/Down move through the results
without leaving the search field.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
)

from config import get_config
from core.date_format import parse_iso, relative_stamp
from core.fuzzy import match_score
from core.i18n import tr
from core.logger import get_logger
from domain.recipe import BUILTIN_RECIPES, Recipe
from ui.i18n_helpers import Retranslator
from ui.library_view import display_name
from ui.option_labels import recipe_label
from ui.theme import get_theme
from utils import format_duration

logger = get_logger(__name__)

# Extra item roles: the right-aligned hint (shortcut, date) and whether a
# row is a group header (no payload, not selectable).
_HINT_ROLE = Qt.ItemDataRole.UserRole + 1
_HEADER_ROLE = Qt.ItemDataRole.UserRole + 2

_RECENT_RECORDS = 6
_MAX_RECORDS = 20


class _PaletteDelegate(QStyledItemDelegate):
    """Paints group headers small and muted, and a row's hint (an
    action's shortcut, a record's date) right-aligned in its row."""

    def paint(self, painter, option, index) -> None:  # noqa: N802
        theme = get_theme()
        if index.data(_HEADER_ROLE):
            painter.save()
            font = QFont(option.font)
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(theme.text_muted))
            rect = option.rect.adjusted(8, 6, -8, 0)
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             index.data(Qt.ItemDataRole.DisplayRole))
            painter.restore()
            return
        hint = index.data(_HINT_ROLE)
        if hint:
            # Leave room for the hint so a long label elides before it.
            option = type(option)(option)
            self.initStyleOption(option, index)
            hint_width = option.fontMetrics.horizontalAdvance(hint) + 28
            option.text = option.fontMetrics.elidedText(
                option.text, Qt.TextElideMode.ElideRight,
                max(0, option.rect.width() - hint_width - 16),
            )
            widget = option.widget
            style = widget.style() if widget is not None else None
            if style is not None:
                style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, widget)
            else:
                super().paint(painter, option, index)
            painter.save()
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            painter.setPen(QColor("#ffffff" if selected else theme.text_muted))
            painter.drawText(
                option.rect.adjusted(0, 0, -12, 0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, hint,
            )
            painter.restore()
            return
        super().paint(painter, option, index)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        size = super().sizeHint(option, index)
        if index.data(_HEADER_ROLE):
            return QSize(size.width(), size.height() + 4)
        return QSize(size.width(), max(size.height(), 30))


class _SearchKeys(QObject):
    """Up/Down/PageUp/PageDown in the search field move the selection."""

    def __init__(self, palette: "CommandPalette") -> None:
        super().__init__(palette)
        self._palette = palette

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            steps = {
                Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1,
                Qt.Key.Key_PageDown: 8, Qt.Key.Key_PageUp: -8,
            }
            step = steps.get(Qt.Key(event.key()))
            if step is not None:
                self._palette.move_selection(step)
                return True
        return False


class CommandPalette(QDialog):
    """Keyboard-first search overlay backed by the existing history FTS.

    Generic actions (as opposed to records/recipes/retriable steps) come
    from bind_actions() — the same QAction objects MainWindow._init_menu_bar
    (B12, docs/IMPROVEMENT_PLAN_2026-08.ru.md) put in the menu bar, marked
    there as palette-eligible. A palette row shows exactly action.text()
    and activating it calls action.trigger(): the same QAction, so it
    can't diverge from what the menu bar does.
    """

    # record id, artifact type ("" for the transcript itself — B7, matches
    # LibraryView.open_record; a materials hit carries its own type so
    # MainWindow._open_record_view can pick the right tab).
    record_requested = pyqtSignal(int, str)
    recipe_requested = pyqtSignal(str)
    retry_step_requested = pyqtSignal(str)
    bookmark_requested = pyqtSignal(int, float)  # record id, seconds

    # Local copy of LibraryView's artifact-type labels (B7) — kept
    # independent rather than importing a private name from ui.library_view.
    _MATERIAL_LABEL_KEYS = {
        "article": "library_chip_article",
        "insights": "library_chip_insights",
        "youtube": "library_chip_youtube",
        "book": "library_chip_book",
        "notes": "library_chip_notes",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._run_view = None
        self._actions: list = []
        self._bookmarks_provider = None
        self._i18n = Retranslator()
        self.setWindowTitle(tr("command_palette_title"))
        self.setModal(True)
        self.resize(640, 460)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        title = self._i18n.text(QLabel(), "command_palette_title")
        title.setProperty("role", "page-title")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setProperty("role", "palette-search")
        self._i18n.text(self.search, "command_palette_placeholder", "setPlaceholderText")
        self._i18n.text(self, "command_palette_title", "setWindowTitle")
        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self._activate_current)
        self.search.installEventFilter(_SearchKeys(self))
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.setItemDelegate(_PaletteDelegate(self.results))
        self.results.setUniformItemSizes(False)
        self.results.itemActivated.connect(self._activate)
        layout.addWidget(self.results, stretch=1)
        hint = self._i18n.text(QLabel(), "command_palette_footer")
        hint.setProperty("role", "dim")
        layout.addWidget(hint)
        self._i18n.bind()

    def bind_run_view(self, run_view) -> None:
        """*run_view* is asked for its currently retriable steps (B8) each
        time the palette refreshes — a plain reference, not a copy, so it
        always reflects whatever run is bound at query time."""
        self._run_view = run_view

    def bind_bookmarks(self, provider) -> None:
        """*provider()* returns the open record's bookmarks (R3), read at
        query time like bind_run_view()."""
        self._bookmarks_provider = provider

    def _bookmark_rows(self, query: str, store) -> list:
        rows = []
        seen: set = set()
        own: list = list(self._bookmarks_provider()) if self._bookmarks_provider is not None else []
        for bookmark in own:
            label = tr("command_bookmark", time=format_duration(bookmark.at_seconds), note=bookmark.note)
            score = match_score(query, label)
            if score is not None:
                # The open record's bookmarks keep their time order.
                rows.append((-bookmark.at_seconds / 1e6, label.strip(), "",
                             ("bookmark", (bookmark.record_id, bookmark.at_seconds)), True))
                seen.add(bookmark.id)
        if query and store is not None:
            for bookmark in store.search_bookmarks(query):
                if bookmark.id in seen:
                    continue
                label = tr("command_bookmark", time=format_duration(bookmark.at_seconds), note=bookmark.note)
                rows.append((-1.0, label.strip(), display_name(bookmark.record_name),
                             ("bookmark", (bookmark.record_id, bookmark.at_seconds)), True))
        return rows

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

    # ── building the list ────────────────────────────────────────────

    def _add_header(self, text: str) -> None:
        item = QListWidgetItem(text)
        item.setData(_HEADER_ROLE, True)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.results.addItem(item)

    def _add_group(self, title: str, rows: list) -> None:
        """*rows*: (score, label, hint, payload, enabled) — best first."""
        if not rows:
            return
        self._add_header(title)
        for _score, label, hint, payload, enabled in sorted(rows, key=lambda r: -r[0]):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, payload)
            if hint:
                item.setData(_HINT_ROLE, hint)
            # Shown, not hidden — a user should see the function exists
            # even when it isn't applicable right now (see
            # docs/IMPROVEMENT_PLAN_2026-08.ru.md, B12 item 4).
            if not enabled:
                item.setFlags(
                    item.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable
                )
            self.results.addItem(item)

    def _action_rows(self, query: str) -> list:
        rows = []
        for action in self._actions:
            label = action.text()
            score = match_score(query, label)
            if score is None:
                continue
            shortcut = action.shortcut()
            hint = shortcut.toString(QKeySequence.SequenceFormat.NativeText) if not shortcut.isEmpty() else ""
            rows.append((score, label, hint, ("action", action), action.isEnabled()))
        return rows

    def _recipe_rows(self, query: str) -> list:
        rows = []
        recipes = list(BUILTIN_RECIPES) + [Recipe.from_dict(e) for e in get_config().recipes]
        for recipe in recipes:
            key = recipe.builtin_key or recipe.name
            label = tr("command_run_recipe", name=recipe_label(recipe))
            score = match_score(query, label)
            if score is not None:
                rows.append((score, label, "", ("recipe", key), True))
        return rows

    def _step_rows(self, query: str) -> list:
        rows: list = []
        if self._run_view is None:
            return rows
        for name, step_label in self._run_view.retriable_steps():
            label = tr("command_retry_step", name=step_label)
            score = match_score(query, label)
            if score is not None:
                rows.append((score, label, "", ("retry_step", name), True))
        return rows

    def _refresh(self, query: str) -> None:
        self.results.clear()
        query = query.strip()
        record_rows: list = []
        material_rows: list = []
        store = None
        try:
            from core.history import get_history_store

            store = get_history_store()
            now = datetime.now()
            records = store.search(query) if query else store.list(limit=_RECENT_RECORDS)
            for rank, record in enumerate(records[:_MAX_RECORDS]):
                # Under its "Records" header the row is just the name.
                name = display_name(getattr(record, "title", "") or record.source_name)
                when = parse_iso(record.created_at)
                hint = relative_stamp(when, now) if when is not None else ""
                # Keep the store's own order (recency or FTS rank).
                record_rows.append((-rank, name, hint, ("record", record.id), True))
            # Materials search (B7): only meaningful for an actual query —
            # search_artifacts("") returns nothing, same contract as the
            # Library's own scope toggle.
            if query:
                for rank, hit in enumerate(store.search_artifacts(query)[:_MAX_RECORDS]):
                    type_label = tr(self._MATERIAL_LABEL_KEYS.get(hit.type, hit.type))
                    label = tr("command_material", type=type_label, name=hit.source_name)
                    material_rows.append((-rank, label, "", ("material", (hit.record_id, hit.type)), True))
        except Exception as exc:  # noqa: BLE001 - the palette still offers commands
            logger.warning("Command palette: history search failed: %s", exc)

        if query:
            self._add_group(tr("command_group_actions"), self._action_rows(query))
            self._add_group(tr("command_group_bookmarks"), self._safe_bookmark_rows(query, store))
            self._add_group(tr("command_group_recipes"), self._recipe_rows(query))
            self._add_group(tr("command_group_steps"), self._step_rows(query))
            self._add_group(tr("command_group_records"), record_rows)
            self._add_group(tr("command_group_materials"), material_rows)
        else:
            self._add_group(tr("command_group_bookmarks"), self._safe_bookmark_rows("", store))
            self._add_group(tr("command_group_recent"), record_rows)
            self._add_group(tr("command_group_actions"), self._action_rows(""))
            self._add_group(tr("command_group_recipes"), self._recipe_rows(""))
            self._add_group(tr("command_group_steps"), self._step_rows(""))
        self.results.setCurrentRow(-1)
        self.move_selection(1)

    def _safe_bookmark_rows(self, query: str, store) -> list:
        try:
            return self._bookmark_rows(query, store)
        except Exception as exc:  # noqa: BLE001 - the palette still offers everything else
            logger.warning("Command palette: bookmarks unavailable: %s", exc)
            return []

    def move_selection(self, step: int) -> None:
        """Move the current row by *step* selectable rows (headers and
        disabled rows are skipped), stopping at either end."""
        count = self.results.count()
        if not count:
            return
        row = self.results.currentRow()
        direction = 1 if step > 0 else -1
        remaining = abs(step)
        candidate = row
        target = row
        while remaining:
            candidate += direction
            if candidate < 0 or candidate >= count:
                break
            item = self.results.item(candidate)
            if item is not None and item.flags() & Qt.ItemFlag.ItemIsSelectable:
                target = candidate
                remaining -= 1
        if target != row and target >= 0:
            self.results.setCurrentRow(target)
            self.results.scrollToItem(self.results.item(target))

    def _activate_current(self) -> None:
        item = self.results.currentItem()
        if item is not None:
            self._activate(item)

    def _activate(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if payload is None:  # a group header
            return
        kind, value = payload
        if kind == "action" and not value.isEnabled():
            return
        self.accept()
        if kind == "record":
            self.record_requested.emit(int(value), "")
        elif kind == "material":
            record_id, artifact_type = value
            self.record_requested.emit(int(record_id), str(artifact_type))
        elif kind == "recipe":
            self.recipe_requested.emit(str(value))
        elif kind == "retry_step":
            self.retry_step_requested.emit(str(value))
        elif kind == "bookmark":
            record_id, seconds = value
            self.bookmark_requested.emit(int(record_id), float(seconds))
        else:
            value.trigger()
