"""One persistent operation, queue, LLM and compute status surface."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from ui.components import StatusBadge


class _QueueOverlay(QDialog):
    """Floating card hosting the batch and course-capture queue panels.

    Anchored above its trigger button instead of being embedded in the
    status bar's own layout — the previous embedded popup grew the status
    bar's height whenever opened, shrinking the workspace above it (see
    docs/UI_REDESIGN_PLAN_2026-09.ru.md, A6). A non-modal QDialog rather
    than a ``Qt.WindowType.Popup`` widget: the latter is this project's
    documented pattern for a floating panel (TranscribeOptionsPopover) but
    its non-embedded branch is never actually constructed anywhere, and it
    turns out not to survive being shown-then-resized cleanly on the
    offscreen QPA platform this app's tests and gallery run under —
    CommandPalette's QDialog is the pattern the gallery already proves
    stable for exactly that sequence.

    Batch (file/book) processing and course capture are shown one at a
    time behind a small switcher instead of two separate status-bar
    buttons/popups — one queue surface, same as everything else this
    phase merges. When course capture is unavailable
    (``Config.live_transcription_enabled`` is off) the switcher itself is
    hidden and the overlay just shows the batch panel, same as before
    course capture existed.
    """

    def __init__(self, batch_widget: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.setModal(False)
        self.setProperty("role", "card")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._switcher = QWidget()
        switcher_layout = QHBoxLayout(self._switcher)
        switcher_layout.setContentsMargins(0, 0, 0, 0)
        switcher_layout.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._files_btn = QPushButton(tr("queue_tab_files"))
        self._files_btn.setCheckable(True)
        self._files_btn.setChecked(True)
        self._files_btn.setProperty("role", "quick-chip")
        self._files_btn.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        self._group.addButton(self._files_btn)
        switcher_layout.addWidget(self._files_btn)
        self._course_btn = QPushButton(tr("queue_tab_course"))
        self._course_btn.setCheckable(True)
        self._course_btn.setProperty("role", "quick-chip")
        self._course_btn.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        self._group.addButton(self._course_btn)
        switcher_layout.addWidget(self._course_btn)
        switcher_layout.addStretch()
        self._switcher.setVisible(False)
        root.addWidget(self._switcher)

        # QStackedWidget manages each page's visibility itself (only the
        # current page is shown) — do not call setVisible() on pages
        # directly, that fights the stack and shows both at once.
        self._stack = QStackedWidget()
        self._stack.addWidget(batch_widget)
        root.addWidget(self._stack, stretch=1)

        self.resize(420, 460)

    def set_course_widget(self, widget: QWidget) -> None:
        self._stack.addWidget(widget)
        self.set_course_available(True)

    def set_course_available(self, available: bool) -> None:
        self._switcher.setVisible(available and self._stack.count() > 1)
        if not available and self._stack.count() > 1:
            self._files_btn.setChecked(True)
            self._stack.setCurrentIndex(0)

    def show_above(self, anchor: QWidget) -> None:
        pos = anchor.mapToGlobal(anchor.rect().topLeft())
        self.move(pos.x(), pos.y() - self.height() - 6)
        self.show()
        self.raise_()
        self.activateWindow()


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
        self._overlay: _QueueOverlay | None = None
        self._batch_count = 0
        self._course_count = 0

    def add_detail_widget(self, widget: QWidget) -> None:
        self.detail_layout.addWidget(widget)

    def bind_queue(self, widget: QWidget) -> None:
        self._overlay = _QueueOverlay(widget, self)

    def bind_course(self, widget: QWidget) -> None:
        if self._overlay is None:
            raise RuntimeError("bind_queue() must be called before bind_course()")
        self._overlay.set_course_widget(widget)

    def set_course_available(self, available: bool) -> None:
        """Whether Course Capture is enabled at all
        (``Config.live_transcription_enabled``) — hides the overlay's
        switcher entirely rather than leaving a dead tab to click."""
        if self._overlay is not None:
            self._overlay.set_course_available(available)

    def _toggle_queue(self) -> None:
        self.show_queue(self._overlay is None or not self._overlay.isVisible())
        self.queue_requested.emit()

    def show_queue(self, visible: bool) -> None:
        if self._overlay is None:
            return
        if visible:
            self._overlay.show_above(self.queue_button)
        else:
            self._overlay.reject()

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
        self._batch_count = count
        self._sync_queue_count()

    def set_course_count(self, count: int) -> None:
        self._course_count = count
        self._sync_queue_count()

    def _sync_queue_count(self) -> None:
        self.queue_button.setText(
            tr("status_queue", count=self._batch_count + self._course_count)
        )

    def clear(self) -> None:
        self.status_label.setText(tr("status_idle"))
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        # Infrastructure status is intentionally always present. Existing
        # operation call sites may still try to hide the retired bar.
        super().setVisible(True)
