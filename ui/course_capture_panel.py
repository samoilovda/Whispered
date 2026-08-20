"""Whispered UI - Course Capture Panel

Records a queue of lessons one at a time by capturing another app's system
audio (e.g. a browser tab playing a purchased course video) through the
existing Live pipeline (core.live.runtime.LiveRuntime) — the same
ScreenCaptureKit-backed path used for meeting transcription, just pointed
at a queue of named lessons instead of one continuous call. This captures
audio from content the user is already legitimately watching; it does not
download, descramble, or otherwise touch the source site.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config import get_config
from core.i18n import tr
from core.live.runtime import LiveRuntime
from core.logger import get_logger
from domain.course_capture import CaptureItemStatus, CaptureQueue, CaptureQueueItem
from domain.transcription import TranscriptionResult
from ui.empty_state import EmptyStateWidget
from ui.live_setup_panel import LiveSetupPanel
from ui.theme import set_role
from ui.toast import show_toast

logger = get_logger(__name__)

STATUS_ICONS = {
    CaptureItemStatus.PENDING: "○",
    CaptureItemStatus.RECORDING: "●",
    CaptureItemStatus.DONE: "✓",
    CaptureItemStatus.ERROR: "×",
}

STATUS_ROLES = {
    CaptureItemStatus.PENDING: "muted",
    CaptureItemStatus.RECORDING: "warning-text",
    CaptureItemStatus.DONE: "success-text",
    CaptureItemStatus.ERROR: "danger-text",
}


class CourseCaptureItemWidget(QWidget):
    """One lesson row: status icon, title, remove button."""

    remove_requested = pyqtSignal(str)

    def __init__(self, item: CaptureQueueItem, parent=None) -> None:
        super().__init__(parent)
        self.item_id = item.id
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self.status_label = QLabel()
        self.status_label.setFixedWidth(20)
        layout.addWidget(self.status_label)

        self.title_label = QLabel()
        self.title_label.setProperty("role", "muted")
        layout.addWidget(self.title_label, stretch=1)

        self.remove_btn = QPushButton("×")
        self.remove_btn.setFixedSize(20, 20)
        self.remove_btn.setProperty("variant", "danger")
        self.remove_btn.setAccessibleName(tr("course_remove"))
        self.remove_btn.setToolTip(tr("course_remove"))
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self.item_id))
        layout.addWidget(self.remove_btn)

        self.update_display(item)

    def update_display(self, item: CaptureQueueItem) -> None:
        self.status_label.setText(STATUS_ICONS.get(item.status, "?"))
        title = item.title
        if item.status is CaptureItemStatus.ERROR and item.error:
            title = f"{title} — {item.error}"
        self.title_label.setText(title)
        role = STATUS_ROLES.get(item.status, "muted")
        set_role(self.title_label, role)
        set_role(self.status_label, role)
        self.remove_btn.setEnabled(item.status is not CaptureItemStatus.RECORDING)


class CourseCapturePanel(QWidget):
    """Panel for capturing a course's lessons one at a time."""

    queue_changed = pyqtSignal(int)
    lesson_saved = pyqtSignal(int)  # history record id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.queue = CaptureQueue()
        self._runtime = LiveRuntime(self)
        self._active_item_id: str | None = None
        self._setup_ui()
        self._connect_runtime()
        self._refresh_list()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header_layout = QHBoxLayout()
        header_label = QLabel(tr("course_capture_title"))
        header_label.setProperty("role", "section-title")
        header_layout.addWidget(header_label)
        self.count_label = QLabel(tr("course_capture_summary", pending=0, done=0, error=0))
        self.count_label.setProperty("role", "dim")
        header_layout.addWidget(self.count_label)
        header_layout.addStretch()
        self.add_btn = QPushButton(tr("course_add_lesson"))
        self.add_btn.clicked.connect(self._add_lesson)
        header_layout.addWidget(self.add_btn)
        layout.addLayout(header_layout)

        hint = QLabel(tr("course_capture_hint"))
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.setup = LiveSetupPanel()
        self.setup.mic_check.setChecked(False)
        layout.addWidget(self.setup)

        self.item_list = QListWidget()
        self.item_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.item_list.model().rowsMoved.connect(self._sync_visual_order)
        layout.addWidget(self.item_list, stretch=1)

        self.empty_state = EmptyStateWidget(
            "microphone",
            tr("course_empty_title"),
            tr("course_empty_hint"),
            tr("course_add_lesson"),
        )
        self.empty_state.action_button.clicked.connect(self._add_lesson)
        layout.addWidget(self.empty_state, stretch=1)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(4)
        self.start_stop_btn = QPushButton(tr("course_start"))
        self.start_stop_btn.setProperty("variant", "primary")
        self.start_stop_btn.clicked.connect(self._toggle_capture)
        self.start_stop_btn.setEnabled(False)
        actions_layout.addWidget(self.start_stop_btn)
        self.clear_btn = QPushButton(tr("course_clear"))
        self.clear_btn.clicked.connect(self._clear_queue)
        self.clear_btn.setEnabled(False)
        actions_layout.addWidget(self.clear_btn)
        layout.addLayout(actions_layout)

    def _connect_runtime(self) -> None:
        self._runtime.finished.connect(self._on_runtime_finished)
        self._runtime.error_occurred.connect(self._on_runtime_error)
        self._runtime.session_state_changed.connect(self._on_session_state_changed)

    # -------------------------------------------------------------- queue

    def _add_lesson(self) -> None:
        default_title = tr("course_lesson_default_title", number=len(self.queue.items) + 1)
        title, ok = QInputDialog.getText(
            self, tr("course_add_lesson"), tr("course_add_lesson_prompt"), text=default_title
        )
        if not ok:
            return
        title = title.strip() or default_title
        self.queue.add_item(title)
        self._refresh_list()

    def _remove_item(self, item_id: str) -> None:
        if item_id == self._active_item_id:
            return
        self.queue.remove_item(item_id)
        self._refresh_list()

    def _clear_queue(self) -> None:
        if self._active_item_id is not None:
            return
        self.queue.items = [
            item for item in self.queue.items if item.status is CaptureItemStatus.RECORDING
        ]
        self._refresh_list()

    def _sync_visual_order(self, *_args) -> None:
        ids: list[str] = []
        for row in range(self.item_list.count()):
            widget = self.item_list.itemWidget(self.item_list.item(row))
            if isinstance(widget, CourseCaptureItemWidget):
                ids.append(widget.item_id)
        by_id = {item.id: item for item in self.queue.items}
        if set(ids) == set(by_id):
            self.queue.items = [by_id[item_id] for item_id in ids]

    def _refresh_list(self) -> None:
        self.item_list.clear()
        for item in self.queue.items:
            widget = CourseCaptureItemWidget(item)
            widget.remove_requested.connect(self._remove_item)
            list_item = QListWidgetItem(self.item_list)
            list_item.setSizeHint(widget.sizeHint())
            self.item_list.addItem(list_item)
            self.item_list.setItemWidget(list_item, widget)

        total = len(self.queue.items)
        done = sum(item.status is CaptureItemStatus.DONE for item in self.queue.items)
        errors = sum(item.status is CaptureItemStatus.ERROR for item in self.queue.items)
        pending = max(0, total - done - errors - (1 if self._active_item_id else 0))
        self.count_label.setText(
            tr("course_capture_summary", pending=pending, done=done, error=errors)
        )
        self.item_list.setVisible(total > 0)
        self.empty_state.setVisible(total == 0)
        self.add_btn.setVisible(total > 0)
        is_capturing = self._active_item_id is not None
        self.clear_btn.setEnabled(total > 0 and not is_capturing)
        has_next = self.queue.next_pending() is not None
        self.start_stop_btn.setEnabled(is_capturing or has_next)
        if not is_capturing:
            self.start_stop_btn.setToolTip(
                "" if has_next else tr("tooltip_course_start_disabled_empty")
            )
        self.setup.set_locked(is_capturing)
        self.queue_changed.emit(total)

    def _update_item_widget(self, item_id: str) -> None:
        item = None
        for row in range(self.item_list.count()):
            widget = self.item_list.itemWidget(self.item_list.item(row))
            if isinstance(widget, CourseCaptureItemWidget) and widget.item_id == item_id:
                item = self.queue.item_by_id(item_id)
                widget.update_display(item)
                return

    # ----------------------------------------------------------- capture

    def _toggle_capture(self) -> None:
        if self._active_item_id is not None:
            self._runtime.stop()
            return
        item = self.queue.next_pending()
        if item is None:
            return
        use_mic, _ = self.setup.selected_sources()
        target = self.setup.selected_target()
        started = self._runtime.start(
            use_mic=use_mic,
            use_system=True,
            model_name=self.setup.selected_model(),
            language=self.setup.selected_language(),
            mic_device=self.setup.selected_mic_device(),
            target=target.capture_target() if target else None,
        )
        if not started:
            show_toast(self, tr("tooltip_course_start_disabled_busy"), "warning")
            return
        self.queue.start_recording(item.id)
        self._active_item_id = item.id
        self._update_item_widget(item.id)
        self._refresh_list()

    def _on_session_state_changed(self, state: str) -> None:
        if self._active_item_id is None:
            return
        can_stop = state in ("running", "paused")
        self.start_stop_btn.setEnabled(can_stop or state in ("failed",))
        self.start_stop_btn.setText(tr("course_stop") if can_stop else tr("course_start"))

    def _on_runtime_finished(self, result: TranscriptionResult, _output_path: str) -> None:
        item_id = self._active_item_id
        if item_id is None:
            return
        self._active_item_id = None
        item = self.queue.finish_recording(item_id, result)
        self.start_stop_btn.setText(tr("course_start"))
        self._save_to_history(item, result)
        self._update_item_widget(item_id)
        self._refresh_list()

    def _on_runtime_error(self, source: str, message: str) -> None:
        item_id = self._active_item_id
        if item_id is None or source not in ("asr", "session", "system"):
            return
        if not self._runtime.is_running():
            self._active_item_id = None
            self.queue.fail_recording(item_id, message)
            self.start_stop_btn.setText(tr("course_start"))
            self._update_item_widget(item_id)
            self._refresh_list()
            show_toast(self, message, "error")

    def _save_to_history(self, item: CaptureQueueItem, result: TranscriptionResult) -> None:
        try:
            from core.history import get_history_store

            record_id = get_history_store().add(
                result,
                source_path="",
                model=get_config().default_model,
                source_kind="live",
                source_name=item.title,
            )
            self.lesson_saved.emit(record_id)
        except Exception:
            logger.exception("Failed to save captured lesson %r to history", item.title)

    # -------------------------------------------------------------- misc

    def shutdown(self) -> None:
        """Part of the Shutdownable protocol (ui/shutdownable.py)."""
        if self._runtime.is_running():
            self._runtime.cancel()
        self._runtime.shutdown()
        self.setup.shutdown()
