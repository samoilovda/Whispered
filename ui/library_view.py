"""
Whispered – Library View
Top-level "Library" section: search + list of past transcriptions.

Supersedes the old History tab now that the sidebar makes this a
first-class section (the default screen on startup) rather than one tab
buried among eight others.
"""

from __future__ import annotations

import re
from datetime import datetime

from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal

from core.logger import get_logger
from core.i18n import tr
from utils import format_duration
from ui.empty_state import EmptyStateWidget
from ui.icons import get_icon, IconColors
from ui.components import FlowLayout, apply_soft_shadow

logger = get_logger(__name__)


_fmt_duration = format_duration


def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%d %b %Y, %H:%M")
    except Exception:
        return iso


_JSON_KEY_RE = re.compile(r'"[^"]+"\s*:\s*')


def _clean_snippet(raw: str) -> str:
    """Strip JSON structure from an FTS5 snippet to produce readable text."""
    text = _JSON_KEY_RE.sub("", raw)
    text = re.sub(r'[\[{}\],"]', " ", text)
    text = " ".join(text.split())
    return text


_ARTIFACT_LABEL_KEYS = {
    "transcript": "library_chip_transcript",
    "youtube": "library_chip_youtube",
    "article": "library_chip_article",
}


class RecordItemWidget(QWidget):
    """Custom rich widget for library items, replacing plain multi-line text."""

    open_requested = pyqtSignal()

    def __init__(
        self,
        name: str,
        meta: str,
        artifacts: list[str],
        snippet: str = "",
        kind: str = "file",
        parent=None,
    ):
        super().__init__(parent)
        self.setProperty("role", "card")
        apply_soft_shadow(self)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        # Title/Name
        title_row = QHBoxLayout()
        self.title_label = QLabel(name)
        self.title_label.setProperty("role", "library-item-title")
        self.title_label.setWordWrap(True)
        title_row.addWidget(self.title_label, stretch=1)
        kind_label = QLabel(tr(f"library_filter_{kind}"))
        kind_label.setProperty("role", "chip")
        title_row.addWidget(kind_label)
        self.open_button = QPushButton(tr("btn_open"))
        self.open_button.setProperty("variant", "ghost")
        self.open_button.clicked.connect(self.open_requested.emit)
        title_row.addWidget(self.open_button)
        main_layout.addLayout(title_row)

        # Meta line
        self.meta_label = QLabel(meta)
        self.meta_label.setProperty("role", "library-item-meta")
        main_layout.addWidget(self.meta_label)

        # Badges layout
        badges_layout = QHBoxLayout()
        badges_layout.setSpacing(6)
        badges_layout.setContentsMargins(0, 2, 0, 0)

        for art in artifacts:
            label_text = tr(_ARTIFACT_LABEL_KEYS.get(art, art))
            badge = QLabel(f"✓ {label_text}")
            badge.setProperty("role", f"badge-pill-{art}")
            badges_layout.addWidget(badge)

        badges_layout.addStretch()
        main_layout.addLayout(badges_layout)

        # Snippet (only shown if present)
        if snippet:
            self.snippet_label = QLabel(snippet)
            self.snippet_label.setProperty("role", "dim")
            self.snippet_label.setWordWrap(True)
            main_layout.addWidget(self.snippet_label)


class LibraryView(QWidget):
    """Library section — search box + list of past transcriptions.

    Emits open_record(record_id) so MainWindow can load it into the
    Record view.
    """

    open_record = pyqtSignal(int)  # record id
    open_cover = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = None   # lazy: avoid import at startup if history_enabled=False
        self._records: list = []
        self._active_filter = "all"
        self._open_record_id: int | None = None
        self._setup_ui()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)

        title = QLabel(tr("library_recent_title"))
        title.setProperty("role", "section-title")
        layout.addWidget(title)

        # ── Toolbar ──────────────────────────────────────────────
        toolbar = QHBoxLayout()

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr("history_search_placeholder"))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMinimumWidth(0)
        self._search_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._search_edit.textChanged.connect(self._schedule_search)
        toolbar.addWidget(self._search_edit, stretch=1)

        self._cover_btn = QPushButton(tr("cover_workspace_title"))
        self._cover_btn.clicked.connect(self.open_cover.emit)
        toolbar.addWidget(self._cover_btn)

        self._refresh_btn = QPushButton()
        self._refresh_btn.setIcon(get_icon('refresh', IconColors.default(), 14))
        self._refresh_btn.setFixedWidth(28)
        self._refresh_btn.setToolTip(tr("library_refresh_tooltip"))
        self._refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self._refresh_btn)

        self._more_btn = QPushButton()
        self._more_btn.setIcon(get_icon('more_horizontal', IconColors.default(), 14))
        self._more_btn.setAccessibleName(tr("library_more_actions"))
        self._more_btn.setToolTip(tr("library_more_actions"))
        menu = QMenu(self._more_btn)
        clear_action = menu.addAction(tr("history_clear_all"))
        clear_action.triggered.connect(self._clear_all)
        self._more_btn.setMenu(menu)
        toolbar.addWidget(self._more_btn)

        layout.addLayout(toolbar)

        # A plain QHBoxLayout would squeeze these chips narrower than their
        # own label once the Library column is too narrow to fit all four
        # ("Диктофон" clipped to "iктоф") — FlowLayout wraps to a second
        # row at full width instead (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, A2).
        filters_widget = QWidget()
        filters = FlowLayout(filters_widget, spacing=6)
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        for key in ("all", "file", "recorder", "live"):
            button = QPushButton(tr(f"library_filter_{key}"))
            button.setCheckable(True)
            button.setProperty("role", "quick-chip")
            button.clicked.connect(
                lambda _checked, filter_key=key: self._set_filter(filter_key)
            )
            self._filter_group.addButton(button)
            filters.addWidget(button)
            if key == "all":
                button.setChecked(True)
        layout.addWidget(filters_widget)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self._run_search)

        # ── List ─────────────────────────────────────────────────
        self._list = QListWidget()
        self._list.setSpacing(2)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        self._list.itemDoubleClicked.connect(self._open_selected)
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

        # ── Status bar ───────────────────────────────────────────
        self._status = QLabel()
        self._status.setProperty("role", "muted")
        layout.addWidget(self._status)

    # ------------------------------------------------------------------ public API

    def refresh(self):
        """Reload list from DB (called after a new transcription is saved,
        or when navigating back from the Record view)."""
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
            logger.warning("Library load failed: %s", e)
            self._records = []
        self._populate()

    def _populate(self):
        self._list.clear()
        is_search = bool(self._search_edit.text().strip())
        visible_records = [
            record
            for record in self._records
            if self._active_filter == "all" or _record_kind(record) == self._active_filter
        ]
        for rec in visible_records:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, rec.id)

            name = rec.source_name or rec.source_path
            date = _fmt_date(rec.created_at)
            dur = _fmt_duration(rec.duration)
            lang = rec.language.upper() if rec.language else "?"

            meta = f"{date}  ·  {dur}  ·  {lang}"
            artifacts = rec.artifacts or ["transcript"]
            snippet = ""
            if is_search and rec.preview:
                snippet = _clean_snippet(rec.preview)

            widget = RecordItemWidget(
                name, meta, artifacts, snippet, kind=_record_kind(rec)
            )
            widget.open_requested.connect(
                lambda record_id=rec.id: self.open_record.emit(record_id)
            )
            item.setSizeHint(widget.sizeHint())

            self._list.addItem(item)
            self._list.setItemWidget(item, widget)
            if rec.id == self._open_record_id:
                self._list.setCurrentItem(item)

        total = len(visible_records)
        key = "history_status_plural" if total != 1 else "history_status"
        self._status.setText(tr(key, count=total))

        # The friendly empty state is for "no records exist at all", not
        # "this search has no matches" — the latter already reads clearly
        # from the "0 records" status line above an empty list.
        show_empty_state = total == 0 and not is_search and self._active_filter == "all"
        show_no_results = total == 0 and not show_empty_state
        self._empty_state.setVisible(show_empty_state)
        self._no_results_state.setVisible(show_no_results)
        self._list.setVisible(not show_empty_state and not show_no_results)

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
        self._populate()

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
