"""Central workspace shown before a transcription produces a document."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from core.i18n import tr


class DraftRecord(QWidget):
    """Choose a source in place, then hand the center pane to RecordView."""

    process_requested = pyqtSignal()
    source_changed = pyqtSignal(str)

    def __init__(
        self,
        file_selector: QWidget,
        recorder: QWidget,
        live: QWidget,
        folder: QWidget,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._source_keys = ("file", "recorder", "live", "folder")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 20)
        root.setSpacing(16)
        title = QLabel(tr("draft_title"))
        title.setProperty("role", "page-title")
        root.addWidget(title)
        subtitle = QLabel(tr("draft_subtitle"))
        subtitle.setProperty("role", "muted")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        switcher = QHBoxLayout()
        self._source_group = QButtonGroup(self)
        self._source_group.setExclusive(True)
        self._source_buttons: dict[str, QPushButton] = {}
        for index, key in enumerate(self._source_keys):
            button = QPushButton(tr(f"draft_source_{key}"))
            button.setCheckable(True)
            button.setProperty("role", "quick-chip")
            button.clicked.connect(lambda _checked, i=index, name=key: self.set_source(name, i))
            self._source_group.addButton(button)
            self._source_buttons[key] = button
            switcher.addWidget(button)
        root.addLayout(switcher)

        self.stack = QStackedWidget()
        for widget in (file_selector, recorder, live, folder):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.addWidget(widget)
            page_layout.addStretch()
            self.stack.addWidget(page)
        root.addWidget(self.stack, stretch=1)

        self.process_button = QPushButton(tr("btn_process"))
        self.process_button.setProperty("variant", "primary")
        self.process_button.setEnabled(False)
        self.process_button.setToolTip(tr("tooltip_process_disabled"))
        self.process_button.clicked.connect(self.process_requested.emit)
        root.addWidget(self.process_button)
        self.set_source("file", 0)

    def set_source(self, key: str, index: int | None = None) -> None:
        if key not in self._source_keys:
            return
        index = self._source_keys.index(key) if index is None else index
        self._source_buttons[key].setChecked(True)
        self.stack.setCurrentIndex(index)
        self.process_button.setVisible(key == "file")
        self.source_changed.emit(key)

    def set_process_enabled(self, enabled: bool) -> None:
        self.process_button.setEnabled(enabled)
        self.process_button.setToolTip(
            tr("tooltip_process") if enabled else tr("tooltip_process_disabled")
        )

    def current_source(self) -> str:
        return self._source_keys[self.stack.currentIndex()]
