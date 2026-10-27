"""Task cards from a meeting (N04): edit, accept, finish, dismiss, export.

Embedded in the Insights tab in place of the plain action-item list. It
shows what the model proposed and what the user decided, keeps unknowns
unknown (an empty owner or date is shown as such), says where each task
came from — or that it has no source — and exports Markdown, CSV and, for
accepted tasks with a real date, a calendar file. Nothing is sent anywhere.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from application import task_export
from core.i18n import tr
from core.logger import get_logger
from domain import tasks as t
from ui.i18n_helpers import Retranslator
from ui.qt_util import must
from ui.theme import px
from utils import format_duration

logger = get_logger(__name__)

_STATUS_KEYS = {
    t.PROPOSED: "task_status_proposed", t.ACCEPTED: "task_status_accepted",
    t.DONE: "task_status_done", t.DISMISSED: "task_status_dismissed",
}


class _TaskRow(QWidget):
    changed = pyqtSignal()
    removed = pyqtSignal(str)
    seek_requested = pyqtSignal(int)

    def __init__(self, card: t.TaskCard, parent=None) -> None:
        super().__init__(parent)
        self.card = card
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.status = QComboBox()
        self.status.setAccessibleName(tr("task_status_label"))
        for status in t.STATUSES:
            self.status.addItem(tr(_STATUS_KEYS[status]), status)
        self.status.setCurrentIndex(self.status.findData(card.status))
        self.status.currentIndexChanged.connect(self._status_changed)
        top.addWidget(self.status)
        self.task = QLineEdit(card.task)
        self.task.setAccessibleName(tr("task_text_label"))
        self.task.editingFinished.connect(self._task_changed)
        top.addWidget(self.task, stretch=1)
        if card.origin == t.USER:
            remove = QPushButton("✕")
            remove.setAccessibleName(tr("task_remove"))
            remove.setToolTip(tr("task_remove"))
            remove.clicked.connect(lambda: self.removed.emit(card.id))
            top.addWidget(remove)
        layout.addLayout(top)

        detail = QHBoxLayout()
        detail.setSpacing(6)
        self.owner = QLineEdit(card.owner or "")
        self.owner.setPlaceholderText(tr("task_owner_unknown"))
        self.owner.setAccessibleName(tr("task_owner_label"))
        self.owner.editingFinished.connect(self._owner_changed)
        detail.addWidget(self.owner, stretch=1)
        self.deadline = QLineEdit(card.deadline or "")
        self.deadline.setPlaceholderText(tr("task_deadline_unknown"))
        self.deadline.setAccessibleName(tr("task_deadline_label"))
        self.deadline.editingFinished.connect(self._deadline_changed)
        detail.addWidget(self.deadline, stretch=1)
        layout.addLayout(detail)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setProperty("role", "muted")
        self.note.setStyleSheet(f"font-size: {px(11)}px;")
        layout.addWidget(self.note)
        self.seek_btn: Optional[QPushButton] = None
        if card.start is not None:
            self.seek_btn = QPushButton(format_duration(card.start))
            self.seek_btn.setProperty("role", "timestamp-link")
            self.seek_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.seek_btn.setToolTip(card.quote or tr("task_source_unverified"))
            self.seek_btn.clicked.connect(lambda: self.seek_requested.emit(card.start or 0))
            layout.addWidget(self.seek_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self._render_note()

    def _render_note(self) -> None:
        card = self.card
        parts = []
        if card.quote:
            parts.append(tr("task_source", quote=card.quote))
        elif card.origin == t.USER:
            parts.append(tr("task_no_source_user"))
        elif card.start is not None:
            parts.append(tr("task_source_unverified"))
        else:
            parts.append(tr("task_no_source"))
        if card.deadline and t.parse_deadline(card.deadline) is None:
            parts.append(tr("task_deadline_not_a_date"))
        if not card.in_result and card.origin == t.MODEL:
            parts.append(tr("task_not_in_result"))
        self.note.setText(" · ".join(parts))

    def _touch(self) -> None:
        if self.card.origin == t.MODEL:
            self.card.edited = True
        self._render_note()
        self.changed.emit()

    def _status_changed(self, _index: int) -> None:
        self.card.status = self.status.currentData()
        self.changed.emit()

    def _task_changed(self) -> None:
        text = self.task.text().strip()
        if text and text != self.card.task:
            self.card.task = text
            self._touch()
        elif not text:
            self.task.setText(self.card.task)

    def _owner_changed(self) -> None:
        value = self.owner.text().strip() or None
        if value != self.card.owner:
            self.card.owner = value
            self._touch()

    def _deadline_changed(self) -> None:
        value = self.deadline.text().strip() or None
        if value != self.card.deadline:
            self.card.deadline = value
            self._touch()


class TasksView(QWidget):
    changed = pyqtSignal(list)            # list[TaskCard] — to be stored
    seek_requested = pyqtSignal(int)
    message = pyqtSignal(str, str)        # text, kind — for a toast

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._i18n = Retranslator()
        self._cards: list[t.TaskCard] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)
        self._rows_box = QWidget()
        self._rows = QVBoxLayout(self._rows_box)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(0)
        root.addWidget(self._rows_box)

        add_row = QHBoxLayout()
        self.new_task = QLineEdit()
        self._i18n.text(self.new_task, "task_add_placeholder", "setPlaceholderText")
        self._i18n.text(self.new_task, "task_add_placeholder", "setAccessibleName")
        self.new_task.returnPressed.connect(self._add_task)
        add_row.addWidget(self.new_task, stretch=1)
        self.add_btn = self._i18n.text(QPushButton(), "task_add")
        self.add_btn.clicked.connect(self._add_task)
        add_row.addWidget(self.add_btn)
        self.export_btn = QToolButton()
        self._i18n.text(self.export_btn, "task_export")
        self.export_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self.export_btn)
        self.export_btn.setMenu(self._menu)
        self._md_action = self._i18n.text(must(self._menu.addAction("")), "task_export_md")
        self._md_action.triggered.connect(lambda: self._export("md"))
        self._csv_action = self._i18n.text(must(self._menu.addAction("")), "task_export_csv")
        self._csv_action.triggered.connect(lambda: self._export("csv"))
        self._ics_action = self._i18n.text(must(self._menu.addAction("")), "task_export_ics")
        self._ics_action.triggered.connect(lambda: self._export("ics"))
        add_row.addWidget(self.export_btn)
        root.addLayout(add_row)
        self._refresh_export()
        # Rows are built from the cards, so a language change rebuilds them
        # (nothing is lost: every edit is already on its card).
        self._i18n.call(lambda: self.set_cards(self._cards))
        self._i18n.bind()

    # ------------------------------------------------------------------ data

    def cards(self) -> list[t.TaskCard]:
        return list(self._cards)

    def set_cards(self, cards: list[t.TaskCard]) -> None:
        self._cards = list(cards)
        while self._rows.count():
            item = self._rows.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for card in self._cards:
            row = _TaskRow(card, self._rows_box)
            row.changed.connect(self._row_changed)
            row.removed.connect(self._remove)
            row.seek_requested.connect(self.seek_requested)
            self._rows.addWidget(row)
        self._refresh_export()

    def clear(self) -> None:
        self.set_cards([])

    def _row_changed(self) -> None:
        self._refresh_export()
        self.changed.emit(self.cards())

    def _remove(self, card_id: str) -> None:
        self.set_cards([c for c in self._cards if c.id != card_id])
        self.changed.emit(self.cards())

    def _add_task(self) -> None:
        text = self.new_task.text().strip()
        if not text:
            return
        card = t.new_user_card(text, [c.id for c in self._cards])
        self.new_task.clear()
        self.set_cards(self._cards + [card])
        self.changed.emit(self.cards())

    # ------------------------------------------------------------------ export

    def _refresh_export(self) -> None:
        has_any = bool(self._cards)
        self.export_btn.setEnabled(has_any)
        self._md_action.setEnabled(has_any)
        self._csv_action.setEnabled(has_any)
        calendar = bool(task_export.calendar_candidates(self._cards))
        self._ics_action.setEnabled(calendar)
        self._ics_action.setToolTip("" if calendar else tr("task_export_ics_none"))

    def export_text(self, kind: str) -> str:
        if kind == "md":
            return task_export.to_markdown(self._cards, unknown=tr("task_unknown_dash"))
        if kind == "csv":
            return task_export.to_csv(self._cards)
        return task_export.to_ics(self._cards)

    def _export(self, kind: str) -> None:
        text = self.export_text(kind)
        if not text:
            self.message.emit(tr("task_export_ics_none"), "info")
            return
        suffix = {"md": "md", "csv": "csv", "ics": "ics"}[kind]
        path, _ = QFileDialog.getSaveFileName(self, tr("task_export"), f"tasks.{suffix}")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
        except OSError as exc:
            self.message.emit(str(exc), "error")
            return
        self.message.emit(tr("task_exported", name=path.rsplit("/", 1)[-1]), "success")
