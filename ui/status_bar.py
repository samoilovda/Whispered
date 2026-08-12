"""One persistent operation, queue, LLM and compute status surface."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from core.i18n import tr
from ui.components import StatusBadge


class StatusBar(QFrame):
    cancel_requested = pyqtSignal()
    queue_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", "operation-bar")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(10)
        self.status_label = QLabel(tr("status_idle"))
        self.status_label.setProperty("role", "muted")
        row.addWidget(self.status_label, stretch=1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(300)
        self.progress.setVisible(False)
        row.addWidget(self.progress)
        self.cancel_button = QPushButton(tr("btn_cancel"))
        self.cancel_button.setProperty("variant", "danger")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        row.addWidget(self.cancel_button)
        self.queue_button = QPushButton(tr("status_queue", count=0))
        self.queue_button.setProperty("variant", "ghost")
        self.queue_button.clicked.connect(self._toggle_queue)
        row.addWidget(self.queue_button)
        self.llm_badge = StatusBadge(tr("status_llm_model", model="LM Studio"), "neutral")
        row.addWidget(self.llm_badge)
        self.device_button = QPushButton(tr("device_detecting"))
        self.device_button.setProperty("variant", "ghost")
        row.addWidget(self.device_button)
        root.addLayout(row)
        self.detail_container = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_container)
        self.detail_layout.setContentsMargins(16, 0, 16, 8)
        root.addWidget(self.detail_container)
        self.queue_popup: QWidget | None = None

    def add_detail_widget(self, widget: QWidget) -> None:
        self.detail_layout.addWidget(widget)

    def bind_queue(self, widget: QWidget) -> None:
        self.queue_popup = widget
        self.detail_layout.addWidget(widget)
        widget.setVisible(False)

    def _toggle_queue(self) -> None:
        if self.queue_popup is not None:
            self.show_queue(not self.queue_popup.isVisible())
        self.queue_requested.emit()

    def show_queue(self, visible: bool) -> None:
        if self.queue_popup is not None:
            self.queue_popup.setVisible(visible)

    def set_operation(
        self,
        text: str,
        *,
        progress: int | None = None,
        cancel_text: str = "",
        details_text: str = "",
        cancellable: bool = True,
    ) -> None:
        self.status_label.setText(text)
        self.progress.setVisible(True)
        if progress is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(max(0, min(100, progress)))
        if cancel_text:
            self.cancel_button.setText(cancel_text)
        self.cancel_button.setVisible(cancellable)

    def set_llm_status(self, connected: bool, detail: str = "") -> None:
        model = detail if connected and detail else "LM Studio"
        self.llm_badge.set_status(
            tr("status_llm_model", model=model), "neutral" if connected else "error"
        )
        self.llm_badge.setToolTip(
            "" if connected else tr("status_llm_unavailable")
        )

    def set_queue_count(self, count: int) -> None:
        self.queue_button.setText(tr("status_queue", count=count))

    def clear(self) -> None:
        self.status_label.setText(tr("status_idle"))
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        # Infrastructure status is intentionally always present. Existing
        # operation call sites may still try to hide the retired bar.
        super().setVisible(True)
