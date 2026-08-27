"""Whispered – Transcript Versions Dialog

Non-modal "Версии" dialog (B8, docs/IMPROVEMENT_PLAN_2026-08.ru.md):
lists a record's saved transcript versions, diffs any two selected ones,
and restores a single selected one via restore_requested — MainWindow
owns applying the restore through DocumentSession.apply_result() (see
that signal's docstring) so this dialog stays Qt-widget-only and doesn't
need to know about panels beyond itself.
"""

from __future__ import annotations

import difflib
import html
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core.i18n import tr


def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%d %b %Y, %H:%M")
    except Exception:
        return iso


def _segment_lines(payload: dict) -> list[str]:
    """One line per segment — what the unified diff is computed over (B8
    item 4: "unified diff через difflib.unified_diff по строкам
    сегментов"). Includes the speaker label so a rename shows up in the
    diff too, not just wording changes."""
    lines = []
    for seg in payload.get("segments", []):
        if not isinstance(seg, dict):
            continue
        speaker = seg.get("speaker") or ""
        text = seg.get("text", "")
        lines.append(f"{speaker}: {text}" if speaker else str(text))
    return lines


def _diff_html(old_lines: list[str], new_lines: list[str]) -> str:
    """Unified diff rendered as highlighted HTML — additions/removals get
    a background tint (B8 item 4: "с подсветкой"), a plain <div> per line
    since QTextEdit.setHtml() doesn't need anything fancier."""
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
    if not diff:
        return f"<div>{html.escape(tr('versions_diff_no_changes'))}</div>"
    rows = []
    for line in diff:
        escaped = html.escape(line) or "&nbsp;"
        if line.startswith("+++") or line.startswith("---"):
            rows.append(f'<div style="color:#888;">{escaped}</div>')
        elif line.startswith("+"):
            rows.append(f'<div style="background:#1e3d1e;color:#9fdf9f;">{escaped}</div>')
        elif line.startswith("-"):
            rows.append(f'<div style="background:#3d1e1e;color:#df9f9f;">{escaped}</div>')
        elif line.startswith("@@"):
            rows.append(f'<div style="color:#8ab4f8;">{escaped}</div>')
        else:
            rows.append(f"<div>{escaped}</div>")
    return "".join(rows)


class TranscriptVersionsDialog(QDialog):
    """Lists ``transcript_revisions`` for one record; selecting two rows
    shows their unified diff, selecting one enables "Восстановить"."""

    restore_requested = pyqtSignal(int)  # transcript_revisions.id

    def __init__(self, record_id: int, parent=None):
        super().__init__(parent)
        self._record_id = record_id
        self._revisions: list = []
        self.setWindowTitle(tr("versions_dialog_title"))
        self.setModal(False)
        self.resize(760, 480)
        self._setup_ui()
        self.reload()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        hint = QLabel(tr("versions_dialog_hint"))
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        body = QHBoxLayout()
        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        body.addWidget(self._list, stretch=1)

        self._diff_view = QTextEdit()
        self._diff_view.setReadOnly(True)
        body.addWidget(self._diff_view, stretch=2)
        layout.addLayout(body, stretch=1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._restore_btn = QPushButton(tr("versions_restore_action"))
        self._restore_btn.setEnabled(False)
        self._restore_btn.clicked.connect(self._on_restore_clicked)
        actions.addWidget(self._restore_btn)
        close_btn = QPushButton(tr("btn_close"))
        close_btn.clicked.connect(self.close)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

    def reload(self) -> None:
        """Re-read the version list from history — call after a restore
        (which itself may add a new version) to keep the dialog live."""
        from core.history import get_history_store

        self._list.clear()
        self._diff_view.clear()
        try:
            self._revisions = get_history_store().list_transcript_revisions(self._record_id)
        except Exception:
            self._revisions = []
        for meta in self._revisions:
            delta = meta.size_delta
            delta_text = f"+{delta}" if delta > 0 else str(delta)
            label = tr(
                "versions_row_label",
                date=_fmt_date(meta.created_at), words=meta.word_count, delta=delta_text,
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, meta.id)
            self._list.addItem(item)

    def _on_selection_changed(self) -> None:
        selected = self._list.selectedItems()
        self._restore_btn.setEnabled(len(selected) == 1)
        if len(selected) != 2:
            if len(selected) <= 1:
                self._diff_view.clear()
            return
        id_a, id_b = (item.data(Qt.ItemDataRole.UserRole) for item in selected)
        # Diff old -> new by revision insertion order (id), independent of
        # which row the user clicked first or the list's newest-first order.
        older_id, newer_id = (id_a, id_b) if id_a < id_b else (id_b, id_a)
        self._show_diff(older_id, newer_id)

    def _show_diff(self, older_id: int, newer_id: int) -> None:
        from core.history import get_history_store

        store = get_history_store()
        older = store.get_transcript_revision(older_id)
        newer = store.get_transcript_revision(newer_id)
        if older is None or newer is None:
            self._diff_view.clear()
            return
        self._diff_view.setHtml(_diff_html(_segment_lines(older), _segment_lines(newer)))

    def _on_restore_clicked(self) -> None:
        selected = self._list.selectedItems()
        if len(selected) != 1:
            return
        revision_id = selected[0].data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, tr("versions_restore_action"), tr("versions_restore_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.restore_requested.emit(revision_id)
