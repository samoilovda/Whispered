"""
Whispered – Library View
The persistent left column: search + the list of past transcriptions.

Layout (top to bottom): one tool row — search field, filter menu, overflow
menu; a row of the active filters as removable chips, shown only while
something narrows the list; the record list, grouped by date while
browsing and ranked by relevance while searching; the record count.

The filters used to be three always-visible chip rows (search scope,
source, recipe). In a 280px column they wrapped to eight rows and left
room for about three records out of dozens — the list, which is what the
column is for, lost to controls that are rarely touched. They now live
in one menu, and what is active stays visible as chips.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QKeyEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config import get_config
from core.date_format import date_group, group_label, parse_iso, short_stamp
from core.i18n import tr, tr_count
from core.logger import get_logger
from domain.job import StepStatus
from domain.recipe import BUILTIN_RECIPES, Recipe
from ui.components import ElidedLabel, FlowLayout, KeepOpenMenu
from ui.empty_state import EmptyStateWidget
from ui.i18n_helpers import Retranslator
from ui.icons import IconColors, IconLabel, get_icon
from ui.option_labels import recipe_label as _recipe_display_label
from utils import SUPPORTED_FORMATS, format_duration

logger = get_logger(__name__)


_fmt_duration = format_duration

# Item data role carrying the artifact type of a materials search hit
# ("" for a plain record row) next to the record id in UserRole.
_ARTIFACT_ROLE = Qt.ItemDataRole.UserRole + 1


def _fmt_date(iso: str, now: datetime | None = None) -> str:
    when = parse_iso(iso)
    if when is None:
        return iso
    return short_stamp(when, now or datetime.now())


_JSON_KEY_RE = re.compile(r'"[^"]+"\s*:\s*')


def _clean_snippet(raw: str) -> str:
    """Strip JSON structure from an FTS5 snippet to produce readable text."""
    text = _JSON_KEY_RE.sub("", raw)
    text = re.sub(r'[\[{}\],"]', " ", text)
    text = " ".join(text.split())
    return text


def display_name(name: str) -> str:
    """A record's name for lists and headers: the source file name without
    a media extension ("lecture.mp4" -> "lecture"); anything else as is."""
    suffix = Path(name).suffix.lower()
    return Path(name).stem if suffix in SUPPORTED_FORMATS else name


_ARTIFACT_LABEL_KEYS = {
    "transcript": "library_chip_transcript",
    "youtube": "library_chip_youtube",
    "youtube_upload": "library_chip_youtube_upload",
    "article": "library_chip_article",
    "insights": "library_chip_insights",
    "book": "library_chip_book",
}

_KIND_ICONS = {"file": "music", "recorder": "microphone", "live": "radio"}


def _step_label(name: str) -> str:
    from application.steps import STEP_REGISTRY

    step = STEP_REGISTRY.get(name)
    return tr(step.label_key) if step is not None else name


def _is_resumable(run) -> bool:
    """B2, docs/IMPROVEMENT_PLAN_2026-08.ru.md: a run that stopped short
    — failed outright, or was interrupted by a crash
    (run_store.mark_stale_running_as_interrupted) — with at least one
    recorded step that isn't SUCCEEDED/SKIPPED still has work left for
    "Продолжить" to pick up."""
    if run is None or run.status not in ("failed", "interrupted"):
        return False
    done = (StepStatus.SUCCEEDED.value, StepStatus.SKIPPED.value)
    return any(outcome.get("status") not in done for outcome in run.outcomes.values())


class RecordItemWidget(QWidget):
    """One row of the Library list.

    Title row: source-kind icon, name (elided, full name in the tooltip)
    and, for a run that stopped short, "Продолжить" (B2,
    docs/IMPROVEMENT_PLAN_2026-08.ru.md). Meta row: when, how long,
    language. Badge row, only when there is something to show: generated
    materials and the steps whose last run failed (B8,
    docs/UI_REDESIGN_PLAN_2026-09.ru.md) — glyph plus text, never colour
    alone. A search hit adds its matching snippet underneath.

    The whole row is the click target (the list opens a record on a
    single click), so there is no separate "Open" button.
    """

    resume_requested = pyqtSignal()

    def __init__(
        self,
        name: str,
        meta: str,
        artifacts: list[str],
        snippet: str = "",
        kind: str = "file",
        failed_steps: "list[str] | None" = None,
        resumable: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setProperty("role", "library-item-card")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 6, 8, 6)
        main_layout.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        kind_icon = IconLabel(_KIND_ICONS.get(kind, "music"), IconColors.muted(), 13)
        kind_icon.setToolTip(tr(f"library_filter_{kind}"))
        kind_icon.setAccessibleName(tr(f"library_filter_{kind}"))
        title_row.addWidget(kind_icon)
        self.title_label = ElidedLabel(name)
        self.title_label.setProperty("role", "library-item-title")
        title_row.addWidget(self.title_label, stretch=1)
        if resumable:
            self.resume_button = QPushButton(tr("library_resume_run"))
            self.resume_button.setProperty("role", "accent-badge")
            self.resume_button.clicked.connect(self.resume_requested.emit)
            title_row.addWidget(self.resume_button)
        main_layout.addLayout(title_row)

        if meta:
            self.meta_label = ElidedLabel(meta)
            self.meta_label.setProperty("role", "library-item-meta")
            main_layout.addWidget(self.meta_label)

        shown = [art for art in artifacts if art != "transcript"]
        if shown or failed_steps:
            badges = QWidget()
            badges_layout = FlowLayout(badges, spacing=4)
            badges_layout.setContentsMargins(0, 2, 0, 0)
            for art in shown:
                label_text = tr(_ARTIFACT_LABEL_KEYS.get(art, art))
                badge = QLabel(f"✓ {label_text}")
                badge.setProperty("role", f"badge-pill-{art}")
                badges_layout.addWidget(badge)
            for step_name in failed_steps or ():
                badge = QLabel(f"✗ {_step_label(step_name)}")
                badge.setProperty("role", "badge-pill-error")
                badge.setToolTip(tr("library_failed_step", step=_step_label(step_name)))
                badges_layout.addWidget(badge)
            main_layout.addWidget(badges)

        if snippet:
            self.snippet_label = QLabel(snippet)
            self.snippet_label.setProperty("role", "dim")
            self.snippet_label.setWordWrap(True)
            main_layout.addWidget(self.snippet_label)


class _GroupHeader(QLabel):
    """A date heading row ("Сегодня", "Сентябрь") between record rows."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "list-group-header")


class _RecordList(QListWidget):
    """The record list. A row opens on a single click (``itemClicked``,
    left button only); Return/Enter opens the current row from the
    keyboard. Double-click is not separately wired, so it doesn't open
    the record twice."""

    activate_current = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentItem():
            self.activate_current.emit()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.relayout_rows()

    def relayout_rows(self) -> None:
        """Size every row to the viewport's width and the height its
        widget needs at that width: rows never get wider than the column
        (no horizontal scrolling) and a wrapped snippet gets its lines."""
        width = self.viewport().width() - 2 * self.spacing()
        if width <= 0:
            return
        for row in range(self.count()):
            item = self.item(row)
            widget = self.itemWidget(item)
            if widget is None:
                continue
            layout = widget.layout()
            height = (
                layout.heightForWidth(width)
                if layout is not None and layout.hasHeightForWidth()
                else -1
            )
            if height <= 0:
                height = widget.sizeHint().height()
            item.setSizeHint(QSize(width, height))


class LibraryView(QWidget):
    """Library section — search box + list of past transcriptions.

    Emits open_record(record_id) so MainWindow can load it into the
    Record view.
    """

    # record id, artifact type ("" for the transcript itself — B7,
    # docs/IMPROVEMENT_PLAN_2026-08.ru.md; MainWindow._open_record_view
    # uses the type to pick which tab a materials search hit opens on).
    open_record = pyqtSignal(int, str)
    open_cover = pyqtSignal()
    resume_run = pyqtSignal(int)  # record id (B2)
    record_renamed = pyqtSignal(int, str)  # record id, new title

    _SCOPES = ("all", "transcripts", "materials")
    _SOURCES = ("all", "file", "recorder", "live")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = None   # lazy: avoid import at startup if history_enabled=False
        self._records: list = []
        self._material_hits: list = []
        self._active_filter = "all"
        self._active_recipe_filter = "all"
        self._search_scope = "all"
        self._open_record_id: int | None = None
        self._i18n = Retranslator()
        self._setup_ui()
        self._i18n.call(self._retranslate_library)
        self._i18n.bind()

    def _retranslate_library(self) -> None:
        self._search_edit.setPlaceholderText(tr("history_search_placeholder"))
        self._scope_section.setText(tr("library_search_scope_section"))
        self._source_section.setText(tr("library_filter_source_label"))
        self._recipe_section.setText(tr("library_filter_recipe_label"))
        for key, action in self._scope_actions.items():
            action.setText(tr(f"library_search_scope_{key}"))
        for key, action in self._source_filter_actions.items():
            action.setText(tr(f"library_filter_{key}"))
        self._recipe_filter_all_action.setText(tr("library_filter_all"))
        self._filter_btn.setToolTip(tr("library_filter_tooltip"))
        self._filter_btn.setAccessibleName(tr("library_filter_button"))
        self._reset_filters_btn.setText(tr("library_filters_reset"))
        self._cover_action.setText(tr("library_open_covers"))
        self._refresh_action.setText(tr("library_refresh_tooltip"))
        self._more_btn.setAccessibleName(tr("library_more_actions"))
        self._more_btn.setToolTip(tr("library_more_actions"))
        self._clear_all_action.setText(tr("history_clear_all"))
        self._empty_state.set_texts(tr("library_empty_title"), tr("library_empty_hint"))
        self._no_results_state.set_texts(
            tr("library_no_results_title"), tr("library_no_results_hint")
        )
        self._build_recipe_filter_actions()
        self.refresh()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)

        # ── Tool row: search · filter · more ─────────────────────
        tools = QHBoxLayout()
        tools.setSpacing(4)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr("history_search_placeholder"))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMinimumWidth(0)
        self._search_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._search_edit.addAction(
            get_icon("search", IconColors.muted(), 14),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self._search_edit.textChanged.connect(self._schedule_search)
        tools.addWidget(self._search_edit, stretch=1)

        self._filter_btn = QToolButton()
        self._filter_btn.setProperty("role", "toolbar-icon")
        self._filter_btn.setIcon(get_icon("filter", IconColors.default(), 14))
        self._filter_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._filter_menu = KeepOpenMenu(self._filter_btn)
        self._filter_btn.setMenu(self._filter_menu)
        tools.addWidget(self._filter_btn)

        self._more_btn = QToolButton()
        self._more_btn.setProperty("role", "toolbar-icon")
        self._more_btn.setIcon(get_icon("more_horizontal", IconColors.default(), 14))
        self._more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_menu = QMenu(self._more_btn)
        self._cover_action = more_menu.addAction(tr("library_open_covers"))
        self._cover_action.triggered.connect(self.open_cover.emit)
        self._refresh_action = more_menu.addAction(tr("library_refresh_tooltip"))
        self._refresh_action.triggered.connect(self.refresh)
        more_menu.addSeparator()
        self._clear_all_action = more_menu.addAction(tr("history_clear_all"))
        self._clear_all_action.triggered.connect(self._clear_all)
        self._more_btn.setMenu(more_menu)
        tools.addWidget(self._more_btn)
        layout.addLayout(tools)

        self._build_filter_menu()

        # ── Active filters (only while something narrows the list) ──
        self._active_filters = QWidget()
        self._active_filters_layout = FlowLayout(self._active_filters, spacing=4)
        self._active_filters_layout.setContentsMargins(0, 0, 0, 0)
        self._reset_filters_btn = QPushButton(tr("library_filters_reset"))
        self._reset_filters_btn.setProperty("variant", "ghost")
        self._reset_filters_btn.setProperty("role", "filter-reset")
        self._reset_filters_btn.clicked.connect(self._reset_filters)
        self._active_filters_layout.addWidget(self._reset_filters_btn)
        self._active_filters.setVisible(False)
        layout.addWidget(self._active_filters)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self._run_search)

        # ── List ─────────────────────────────────────────────────
        self._list = _RecordList()
        self._list.setProperty("role", "library-list")
        self._list.setSpacing(1)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        self._list.itemClicked.connect(self._activate_item)
        self._list.activate_current.connect(
            lambda: self._activate_item(self._list.currentItem())
        )
        layout.addWidget(self._list, stretch=1)

        # Shown instead of the list when there are zero records and no
        # active search — an empty QListWidget alone just reads as
        # "still loading", not "nothing here yet".
        self._empty_state = EmptyStateWidget(
            "list", tr("library_empty_title"), tr("library_empty_hint")
        )
        self._empty_state.setVisible(False)
        layout.addWidget(self._empty_state, stretch=1)
        self._no_results_state = EmptyStateWidget(
            "list",
            tr("library_no_results_title"),
            tr("library_no_results_hint"),
        )
        self._no_results_state.setVisible(False)
        layout.addWidget(self._no_results_state, stretch=1)

        # ── Status line ──────────────────────────────────────────
        self._status = QLabel()
        self._status.setProperty("role", "muted")
        layout.addWidget(self._status)

    def _build_filter_menu(self) -> None:
        """Three exclusive groups in one menu: where a search looks, which
        source kind, which recipe. The menu stays open while toggling
        (KeepOpenMenu) so narrowing by two properties is one trip."""
        menu = self._filter_menu
        self._scope_section = menu.addSection(tr("library_search_scope_section"))
        self._scope_group = QActionGroup(self)
        self._scope_actions: dict[str, QAction] = {}
        for key in self._SCOPES:
            action = self._add_choice(menu, self._scope_group, tr(f"library_search_scope_{key}"))
            action.setChecked(key == "all")
            action.triggered.connect(lambda _c=False, k=key: self._set_search_scope(k))
            self._scope_actions[key] = action

        self._source_section = menu.addSection(tr("library_filter_source_label"))
        self._filter_group = QActionGroup(self)
        self._source_filter_actions: dict[str, QAction] = {}
        for key in self._SOURCES:
            action = self._add_choice(menu, self._filter_group, tr(f"library_filter_{key}"))
            action.setChecked(key == "all")
            action.triggered.connect(lambda _c=False, k=key: self._set_filter(k))
            self._source_filter_actions[key] = action
        self._filter_all_action = self._source_filter_actions["all"]

        self._recipe_section = menu.addSection(tr("library_filter_recipe_label"))
        self._recipe_filter_group = QActionGroup(self)
        self._recipe_filter_all_action = self._add_choice(
            menu, self._recipe_filter_group, tr("library_filter_all")
        )
        self._recipe_filter_all_action.setChecked(True)
        self._recipe_filter_all_action.triggered.connect(
            lambda: self._set_recipe_filter("all")
        )
        self._recipe_filter_actions: dict[str, QAction] = {}
        self._build_recipe_filter_actions()

    @staticmethod
    def _add_choice(menu: QMenu, group: QActionGroup, text: str) -> QAction:
        action = QAction(text, menu)
        action.setCheckable(True)
        group.addAction(action)
        menu.addAction(action)
        return action

    # ------------------------------------------------------------------ public API

    def refresh(self):
        """Reload list from DB (called after a new transcription is saved,
        or when navigating back from the Record view)."""
        query = self._search_edit.text().strip()
        self._load(query)

    def clear_all(self):
        """Public entry point for the main menu bar; confirms then delegates."""
        self._clear_all()

    def focus_search(self) -> None:
        """Put the cursor in the search field (Edit > Find in Library)."""
        self._search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._search_edit.selectAll()

    # ------------------------------------------------------------------ internals

    def _get_store(self):
        if self._store is None:
            from core.history import get_history_store
            self._store = get_history_store()
        return self._store

    def _load(self, query: str = ""):
        store = self._get_store()
        self._material_hits = []
        try:
            if query:
                self._records = (
                    store.search(query) if self._search_scope != "materials" else []
                )
                if self._search_scope in ("all", "materials"):
                    self._material_hits = store.search_artifacts(query)
            else:
                # No query: there is no "browse all materials" view — a
                # materials-only scope with an empty search box just shows
                # nothing until the user types something (see
                # HistoryStore.search_artifacts's own empty-query contract).
                self._records = store.list() if self._search_scope != "materials" else []
        except Exception as e:
            logger.warning("Library load failed: %s", e)
            self._records = []
            self._material_hits = []
        self._populate()

    def _latest_runs_for(self, records) -> dict:
        """Latest job_runs row per record (B8, see application/run_store.py),
        keyed by ``str(record.id)`` and fetched in one query for the whole
        page. Records with no run are simply absent; a missing/corrupt
        job_runs table degrades to "no runs" rather than breaking the list."""
        try:
            from application.run_store import load_latest_runs

            return load_latest_runs([record.id for record in records])
        except Exception as exc:
            logger.warning("Failed to load runs for the Library: %s", exc)
            return {}

    def _add_header(self, text: str) -> None:
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        header = _GroupHeader(text)
        item.setSizeHint(header.sizeHint())
        self._list.addItem(item)
        self._list.setItemWidget(item, header)

    def _add_row(self, record_id: int, artifact_type: str, widget: QWidget) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, record_id)
        item.setData(_ARTIFACT_ROLE, artifact_type)
        item.setSizeHint(widget.sizeHint())
        self._list.addItem(item)
        self._list.setItemWidget(item, widget)
        return item

    def _populate(self):
        self._list.clear()
        is_search = bool(self._search_edit.text().strip())
        source_filtered = [
            record
            for record in self._records
            if self._active_filter == "all" or _record_kind(record) == self._active_filter
        ]
        runs = self._latest_runs_for(source_filtered)
        visible_records = []
        for record in source_filtered:
            run = runs.get(str(record.id))
            if self._active_recipe_filter != "all" and (
                run is None or run.recipe != self._active_recipe_filter
            ):
                continue
            visible_records.append((record, run))

        now = datetime.now()
        last_group = None
        for rec, run in visible_records:
            # Search results are ranked by relevance, not date — grouping
            # them by day would scatter the best match under a header.
            when = parse_iso(rec.created_at)
            if not is_search and when is not None:
                group = date_group(when, now)
                if group != last_group:
                    self._add_header(group_label(group, now))
                    last_group = group

            name = display_name(_record_title(rec))
            parts = [
                short_stamp(when, now) if when is not None else rec.created_at,
                _fmt_duration(rec.duration),
            ]
            if rec.language:
                parts.append(rec.language.upper())
            meta = "  ·  ".join(parts)
            snippet = _clean_snippet(rec.preview) if is_search and rec.preview else ""
            failed_steps = [
                step_name for step_name, outcome in (run.outcomes if run else {}).items()
                if outcome.get("status") == StepStatus.FAILED.value
            ]

            widget = RecordItemWidget(
                name, meta, rec.artifacts or ["transcript"], snippet,
                kind=_record_kind(rec),
                failed_steps=failed_steps, resumable=_is_resumable(run),
            )
            widget.resume_requested.connect(
                lambda record_id=rec.id: self.resume_run.emit(record_id)
            )
            item = self._add_row(rec.id, "", widget)
            if rec.id == self._open_record_id:
                self._list.setCurrentItem(item)

        for hit in self._material_hits:
            type_label = tr(_ARTIFACT_LABEL_KEYS.get(hit.type, hit.type))
            name = display_name(hit.source_name or hit.type)
            meta = tr("library_material_hit", type=type_label)
            snippet = _clean_snippet(hit.snippet) if hit.snippet else ""
            widget = RecordItemWidget(
                name, meta, [], snippet, kind=hit.source_kind or "file",
            )
            self._add_row(hit.record_id, hit.type, widget)

        self._list.relayout_rows()

        total = len(visible_records) + len(self._material_hits)
        self._status.setText(tr_count("history_count", total))

        # The friendly empty state is for "no records exist at all", not
        # "this search has no matches".
        show_empty_state = (
            total == 0 and not is_search
            and self._active_filter == "all" and self._active_recipe_filter == "all"
            and self._search_scope != "materials"
        )
        show_no_results = total == 0 and not show_empty_state
        self._empty_state.setVisible(show_empty_state)
        self._no_results_state.setVisible(show_no_results)
        self._list.setVisible(not show_empty_state and not show_no_results)
        self._status.setVisible(not show_empty_state)
        # Nothing to filter yet: the filter menu would only offer empty
        # results, so it waits until there is a record.
        self._filter_btn.setVisible(not show_empty_state)
        self._render_active_filters(is_search)

    def _active_filter_chips(self) -> list[tuple[str, str]]:
        """(kind, display text) for every non-default filter."""
        chips: list[tuple[str, str]] = []
        if self._search_scope != "all":
            chips.append(("scope", tr(f"library_search_scope_{self._search_scope}")))
        if self._active_filter != "all":
            chips.append(("source", tr(f"library_filter_{self._active_filter}")))
        if self._active_recipe_filter != "all":
            action = self._recipe_filter_actions.get(self._active_recipe_filter)
            chips.append(
                ("recipe", action.text() if action is not None else self._active_recipe_filter)
            )
        return chips

    def _render_active_filters(self, is_search: bool) -> None:
        chips = self._active_filter_chips()
        while self._active_filters_layout.count():
            entry = self._active_filters_layout.takeAt(0)
            widget = entry.widget() if entry is not None else None
            if widget is not None and widget is not self._reset_filters_btn:
                widget.deleteLater()
        for kind, text in chips:
            chip = QPushButton(f"{text}  ✕")
            chip.setProperty("role", "filter-chip")
            chip.setAccessibleName(tr("library_filter_remove", name=text))
            chip.setToolTip(tr("library_filter_remove", name=text))
            chip.clicked.connect(lambda _c=False, k=kind: self._clear_one_filter(k))
            self._active_filters_layout.addWidget(chip)
        self._active_filters_layout.addWidget(self._reset_filters_btn)
        self._active_filters.setVisible(bool(chips) or is_search)

        count = len(chips)
        self._filter_btn.setText(str(count) if count else "")
        self._filter_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon if count
            else Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self._filter_btn.setProperty("active", bool(count))
        self._filter_btn.style().unpolish(self._filter_btn)
        self._filter_btn.style().polish(self._filter_btn)
        tooltip = tr("library_filter_tooltip")
        if count:
            tooltip = tr("library_filter_button_active", count=count) + " — " + tooltip
        self._filter_btn.setToolTip(tooltip)

    def _clear_one_filter(self, kind: str) -> None:
        if kind == "scope":
            self._scope_actions["all"].setChecked(True)
            self._set_search_scope("all")
        elif kind == "source":
            self._filter_all_action.setChecked(True)
            self._set_filter("all")
        elif kind == "recipe":
            self._recipe_filter_all_action.setChecked(True)
            self._set_recipe_filter("all")

    def _schedule_search(self, _text: str):
        self._search_timer.start()

    def _run_search(self):
        self._load(self._search_edit.text().strip())

    def set_open_record(self, record_id: int | None) -> None:
        """Keep the document visible as a selection in the persistent list."""
        self._open_record_id = record_id
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == record_id:
                self._list.setCurrentItem(item)
                return
        self._list.setCurrentItem(None)

    def _set_filter(self, filter_key: str) -> None:
        self._active_filter = filter_key
        action = self._source_filter_actions.get(filter_key)
        if action is not None:
            action.setChecked(True)
        self._populate()

    def _set_recipe_filter(self, recipe_key: str) -> None:
        self._active_recipe_filter = recipe_key
        action = (
            self._recipe_filter_all_action if recipe_key == "all"
            else self._recipe_filter_actions.get(recipe_key)
        )
        if action is not None:
            action.setChecked(True)
        self._populate()

    def _set_search_scope(self, scope: str) -> None:
        self._search_scope = scope
        action = self._scope_actions.get(scope)
        if action is not None:
            action.setChecked(True)
        self._load(self._search_edit.text().strip())

    def _build_recipe_filter_actions(self) -> None:
        """(Re)build the built-in + custom recipe entries of the filter
        menu (B4, docs/IMPROVEMENT_PLAN_2026-08.ru.md) after its "All"
        entry, which survives the rebuild."""
        for action in self._recipe_filter_actions.values():
            self._recipe_filter_group.removeAction(action)
            self._filter_menu.removeAction(action)
            action.deleteLater()
        self._recipe_filter_actions = {}
        recipes = list(BUILTIN_RECIPES) + [
            Recipe.from_dict(entry) for entry in get_config().recipes
        ]
        for recipe in recipes:
            key = recipe.builtin_key or recipe.name
            action = self._add_choice(
                self._filter_menu, self._recipe_filter_group, _recipe_display_label(recipe)
            )
            action.triggered.connect(lambda _c=False, k=key: self._set_recipe_filter(k))
            self._recipe_filter_actions[key] = action
        current = self._recipe_filter_actions.get(self._active_recipe_filter)
        (current or self._recipe_filter_all_action).setChecked(True)

    def refresh_recipe_filters(self) -> None:
        """Public entry point for after Config.recipes changes (save,
        save-as-new, delete in the recipe editor) — rebuild the recipe
        entries; if the currently active filter no longer matches any
        recipe (its custom recipe was deleted), fall back to "All" rather
        than silently filtering on a key nothing produces any more."""
        recipes = list(BUILTIN_RECIPES) + [
            Recipe.from_dict(e) for e in get_config().recipes
        ]
        known_keys = {r.builtin_key or r.name for r in recipes}
        if self._active_recipe_filter != "all" and self._active_recipe_filter not in known_keys:
            self._active_recipe_filter = "all"
        self._build_recipe_filter_actions()
        self._populate()

    def _reset_filters(self) -> None:
        """Return every filter to "All" and clear the search field in one
        step (see docs/IMPROVEMENT_PLAN_2026-08.ru.md, A2), instead of
        un-toggling each filter and clearing the text by hand."""
        self._active_filter = "all"
        self._active_recipe_filter = "all"
        self._search_scope = "all"
        self._filter_all_action.setChecked(True)
        self._recipe_filter_all_action.setChecked(True)
        self._scope_actions["all"].setChecked(True)
        # setText alone wouldn't re-run the search; blockSignals avoids a
        # redundant _schedule_search -> debounce -> _run_search round trip
        # for what _load("") below already does synchronously.
        self._search_edit.blockSignals(True)
        self._search_edit.clear()
        self._search_edit.blockSignals(False)
        self._load("")

    def _activate_item(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        record_id = item.data(Qt.ItemDataRole.UserRole)
        if record_id is None:  # a date header
            return
        self.open_record.emit(record_id, item.data(_ARTIFACT_ROLE) or "")

    def _show_context_menu(self, pos: QPoint):
        item = self._list.itemAt(pos)
        if not item or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        record_id = item.data(Qt.ItemDataRole.UserRole)

        menu = QMenu(self)
        open_act = menu.addAction(tr("btn_open"))
        rename_act = menu.addAction(tr("library_rename"))
        menu.addSeparator()
        delete_act = menu.addAction(tr("history_delete_title"))

        action = menu.exec(self._list.mapToGlobal(pos))
        if action == open_act:
            self._activate_item(item)
        elif action == rename_act:
            self.rename_record(record_id)
        elif action == delete_act:
            self._delete_record(record_id)

    def rename_record(self, record_id: int) -> None:
        """Ask for a new display name and store it; the source file and
        the record's output folder keep their names."""
        store = self._get_store()
        try:
            current = store.get_title(record_id) or ""
        except Exception as exc:
            logger.warning("Library rename lookup failed: %s", exc)
            return
        text, ok = QInputDialog.getText(
            self, tr("library_rename_title"), tr("library_rename_prompt"),
            QLineEdit.EchoMode.Normal, display_name(current),
        )
        title = text.strip()
        if not ok or not title or title == display_name(current):
            return
        try:
            store.set_title(record_id, title)
        except Exception as exc:
            logger.warning("Library rename failed: %s", exc)
            return
        self.refresh()
        self.record_renamed.emit(record_id, title)

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
            logger.warning("Library delete failed: %s", e)
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
            logger.info("Library cleared: %d records deleted", n)
        except Exception as e:
            logger.warning("Library clear failed: %s", e)
        self.refresh()


def _record_title(record) -> str:
    """The user's name for a record if they gave one, else its source name."""
    return (
        getattr(record, "title", "")
        or getattr(record, "source_name", "")
        or getattr(record, "source_path", "")
    )


def _record_kind(record) -> str:
    explicit = getattr(record, "source_kind", "")
    if explicit in {"file", "recorder", "live"}:
        return explicit
    path = str(getattr(record, "source_path", ""))
    name = str(getattr(record, "source_name", ""))
    lower_name = name.lower()
    if lower_name.startswith("live-") or lower_name.startswith("zoom-"):
        return "live"
    if name.startswith("REC_") or "/recordings/" in path.replace("\\", "/"):
        return "recorder"
    return "file"
