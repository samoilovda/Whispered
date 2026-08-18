"""Incremental live transcript: one stable row per segment id."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from core.live.contracts import SegmentState, SegmentUpdate
from core.live.presentation import LiveTranscriptModel
from ui.components import StatusBadge
from utils import format_duration


@dataclass
class _Row:
    update: SegmentUpdate
    item: QListWidgetItem
    time: QLabel
    source: QLabel
    state: StatusBadge
    text: QLabel
    flags: QLabel


class LiveTranscriptView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: dict[str, _Row] = {}
        self._model = LiveTranscriptModel()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QHBoxLayout()
        title = QLabel(tr("live_transcript_title"))
        title.setProperty("role", "section-title")
        heading.addWidget(title)
        heading.addStretch()
        self.latest_btn = QPushButton(tr("live_to_latest"))
        self.latest_btn.setVisible(False)
        self.latest_btn.clicked.connect(self.scroll_to_latest)
        heading.addWidget(self.latest_btn)
        layout.addLayout(heading)
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.verticalScrollBar().valueChanged.connect(self._sync_latest_button)
        layout.addWidget(self.list, 1)

    def accept_update(self, update) -> bool:
        if not self._model.accept(update):
            return False
        previous = self._rows.get(update.segment_id)
        at_bottom = self._at_bottom()
        if previous is None:
            row = self._create_row(update)
            self._rows[update.segment_id] = row
        else:
            row = previous
            row.update = update
        self._render(row)
        self._update_relationships()
        if at_bottom:
            self.scroll_to_latest()
        else:
            self.latest_btn.setVisible(True)
        return True

    def _create_row(self, update) -> _Row:
        item = QListWidgetItem(self.list)
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(10, 7, 10, 7)
        time_label = QLabel()
        time_label.setMinimumWidth(48)
        source = QLabel()
        source.setMinimumWidth(84)
        state = StatusBadge()
        text = QLabel()
        text.setWordWrap(True)
        flags = QLabel()
        flags.setProperty("role", "muted")
        layout.addWidget(time_label)
        layout.addWidget(source)
        layout.addWidget(state)
        layout.addWidget(text, 1)
        layout.addWidget(flags)
        item.setSizeHint(widget.sizeHint())
        self.list.setItemWidget(item, widget)
        return _Row(update, item, time_label, source, state, text, flags)

    def _render(self, row: _Row) -> None:
        update = row.update
        source = update.segment.speaker or update.segment_id.split(":", 1)[0]
        row.time.setText(format_duration(update.segment.start))
        row.source.setText(tr("live_source_mic") if source == "mic" else tr("live_zoom"))
        final = update.state is SegmentState.FINAL
        row.state.set_status(tr("live_final") if final else tr("live_partial"),
                             "success" if final else "info")
        row.text.setText(update.segment.text.strip())
        row.item.setSizeHint(self.list.itemWidget(row.item).sizeHint())

    def _update_relationships(self) -> None:
        relationships = self._model.relationships()
        names = {"overlap": tr("live_overlap"), "ambiguous": tr("live_ambiguous")}
        for segment_id, row in self._rows.items():
            values = [names[value] for value in relationships[segment_id]]
            row.flags.setText(" · ".join(values))
            row.flags.setToolTip("\n".join(values))

    def _at_bottom(self) -> bool:
        bar = self.list.verticalScrollBar()
        return bar.maximum() - bar.value() <= 2

    def _sync_latest_button(self) -> None:
        self.latest_btn.setVisible(bool(self._rows) and not self._at_bottom())

    def scroll_to_latest(self) -> None:
        self.list.scrollToBottom()
        self.latest_btn.setVisible(False)

    def reset(self) -> None:
        self._rows.clear()
        self._model.clear()
        self.list.clear()
        self.latest_btn.setVisible(False)
