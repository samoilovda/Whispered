"""Whispered - Cut View
Right-hand tab for video mode: lists transcript segments with checkboxes
so the user can mark which to keep and which to cut before EDL export.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt, pyqtSignal

from core.i18n import tr
from utils import format_timestamp_vtt


class CutView(QWidget):
    """Timeline / Cut tab — segment checklist for video editing."""

    seek_requested = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result = None
        self._segments: list = []
        self._setup_ui()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Header row
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        title = QLabel(tr("cut_view_title"))
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        self._count_label = QLabel("")
        self._count_label.setProperty("role", "muted")
        self._count_label.setStyleSheet("font-size: 11px;")
        header_layout.addWidget(self._count_label)

        select_all_btn = QPushButton(tr("cut_select_all"))
        select_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        select_all_btn.clicked.connect(self._select_all)
        header_layout.addWidget(select_all_btn)

        select_none_btn = QPushButton(tr("cut_select_none"))
        select_none_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        select_none_btn.clicked.connect(self._select_none)
        header_layout.addWidget(select_none_btn)

        layout.addWidget(header)

        # Segment list — uses the global QListWidget rule from theme.py
        self._list = QListWidget()
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._list, stretch=1)

        # Placeholder label (visible when empty)
        self._placeholder = QLabel(tr("cut_placeholder"))
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setProperty("role", "dim")
        self._placeholder.setStyleSheet("font-size: 13px;")
        layout.addWidget(self._placeholder)

    # ------------------------------------------------------------------ public API

    def set_result(self, result) -> None:
        """Populate the list from a TranscriptionResult; all segments checked."""
        self._result = result
        self._segments = list(result.segments)
        self._rebuild()

    def get_kept_segments(self) -> list:
        """Return segments whose checkbox is checked, in original order."""
        kept = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                kept.append(self._segments[i])
        return kept

    def mark_indices(self, indices: list[int]) -> None:
        """Uncheck the given segment indices (e.g. detected pauses/fillers)."""
        self._list.blockSignals(True)
        for i in indices:
            if 0 <= i < self._list.count():
                self._list.item(i).setCheckState(Qt.CheckState.Unchecked)
        self._list.blockSignals(False)
        self._update_count()

    def get_kept_duration(self) -> float:
        """Return total duration (seconds) of checked segments."""
        total = 0.0
        for i in range(self._list.count()):
            if self._list.item(i).checkState() == Qt.CheckState.Checked:
                seg = self._segments[i]
                total += max(0.0, seg.end - seg.start)
        return total

    def clear(self) -> None:
        self._result = None
        self._segments = []
        self._list.clear()
        self._count_label.setText("")
        self._placeholder.show()
        self._list.hide()

    # ------------------------------------------------------------------ internal

    def _rebuild(self):
        self._list.blockSignals(True)
        self._list.clear()
        for seg in self._segments:
            ts = f"[{format_timestamp_vtt(seg.start)} → {format_timestamp_vtt(seg.end)}]"
            text = seg.text.strip()
            label = f"{ts}  {text}"
            item = QListWidgetItem(label)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(Qt.CheckState.Checked)
            self._list.addItem(item)
        self._list.blockSignals(False)

        if self._segments:
            self._placeholder.hide()
            self._list.show()
        else:
            self._placeholder.show()
            self._list.hide()

        self._update_count()

    def _update_count(self):
        total = self._list.count()
        kept = 0
        kept_secs = 0.0
        for i in range(total):
            if self._list.item(i).checkState() == Qt.CheckState.Checked:
                kept += 1
                seg = self._segments[i]
                kept_secs += max(0.0, seg.end - seg.start)
        mins = int(kept_secs) // 60
        secs = int(kept_secs) % 60
        dur_str = f"{mins}:{secs:02d}"
        self._count_label.setText(tr("cut_count", kept=kept, total=total, duration=dur_str))

    def _on_item_changed(self, _item):
        self._update_count()

    def _on_double_click(self, item):
        idx = self._list.row(item)
        if 0 <= idx < len(self._segments):
            self.seek_requested.emit(self._segments[idx].start)

    def _select_all(self):
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Checked)
        self._list.blockSignals(False)
        self._update_count()

    def _select_none(self):
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Unchecked)
        self._list.blockSignals(False)
        self._update_count()
