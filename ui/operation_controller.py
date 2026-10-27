"""What is running in the background, and Cancel for each of it.

Several operations can overlap — a clean-up, a book, a project export, the
queue, a recipe run. This controller lists them (``running``), stops one by
name or all of them (``cancel``) and keeps the status bar's Cancel button
honest: it is shown while anything still runs, offers a menu when more than
one does, and its tooltip names them. It only reads the window's job
fields; each stop goes through the owner's own cancel call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QMenu, QWidget

from application.operations import Operation, cancel_all, cancel_one, describe
from core.i18n import tr
from ui.qt_util import must

if TYPE_CHECKING:
    from ui.main_window import MainWindow

ALL_OPERATIONS = "*"


class _CancelTooltip(QObject):
    """Fills the Cancel button's tooltip with the running operations just
    before it is shown. A filter object of its own, parented to the button:
    the window itself must not be an event filter (it crashed the real-Qt
    suite intermittently)."""

    def __init__(self, button: QWidget, controller: "OperationController") -> None:
        super().__init__(button)
        self._button = button
        self._controller = controller
        button.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._button and event.type() == QEvent.Type.ToolTip:
            ops = self._controller.running()
            self._button.setToolTip(
                tr("cancel_operation_tip", names=describe(ops)) if len(ops) > 1 else "")
        return False


class OperationController:
    def __init__(self, host: "MainWindow") -> None:
        self._host = host
        self._tooltip = _CancelTooltip(host.cancel_btn, self)

    def running(self) -> list[Operation]:
        """Everything running right now, each with the call that stops just that one."""
        host = self._host
        ops: list[Operation] = []
        if host.transcriber.is_busy():
            ops.append(Operation("transcribe", tr("step_transcribe"), host.transcriber.cancel))
        if host.recipe_run.is_running():
            ops.append(Operation("recipe", tr("record_run_running"), host.recipe_run.cancel))
        for key, job, cancel in (
            ("clean", host._clean_job, host._cancel_clean_job),
            ("article", host._article_job, host._cancel_article_job),
            ("insights", host._insights_job, host._cancel_insights_job),
            ("youtube_package", host._youtube_job, host._cancel_youtube_job),
            ("book", host._book_job, host._cancel_book_job),
        ):
            if job is not None and job.isRunning():
                ops.append(Operation(key, tr(f"step_{key}"), cancel, ai=True))
        if host.project.is_busy():
            ops.append(Operation("project", tr("project_title"), host.project.cancel))
        if host.batch_panel._is_processing() or host.queue_controller.is_busy():
            def cancel_batch() -> None:
                host.batch_panel.cancel_processing()
                host.queue_controller.cancel()
            ops.append(Operation("batch", tr("operation_queue"), cancel_batch))
        return ops

    def others_running(self) -> bool:
        """A single-step job, a project export/import or the batch queue is
        still working (the transcription itself is not counted)."""
        host = self._host
        jobs = (host._clean_job, host._article_job, host._insights_job,
                host._youtube_job, host._book_job)
        return (any(job is not None and job.isRunning() for job in jobs)
                or host.project.is_busy() or host.queue_controller.is_busy()
                or host.batch_panel._is_processing())

    def cancel(self, key: Optional[str] = None) -> None:
        """Cancel one named operation; without a *key*, the only running one,
        or — when several overlap — the one the user picks from a menu (or
        all of them)."""
        host = self._host
        ops = self.running()
        if key is None:
            if len(ops) > 1:
                key = self.pick(ops)
                if key is None:
                    return
            elif ops:
                key = ops[0].key
        if key == ALL_OPERATIONS:
            cancel_all(ops)
            host.status_label.setText(tr("status_cancelled"))
        elif key is not None:
            done = cancel_one(ops, key)
            if done is not None:
                host.status_label.setText(tr("status_ai_cancelled" if done.ai else "status_cancelled"))
        else:
            host.transcriber.cancel()
            host.status_label.setText(tr("status_cancelled"))
        host._reset_ui()

    def pick(self, ops: list[Operation]) -> Optional[str]:
        """Menu of what is running (Cancel <name>… / Cancel all); the key of
        the choice, or None when dismissed."""
        host = self._host
        menu = QMenu(host)
        for op in ops:
            must(menu.addAction(tr("cancel_operation_named", name=op.label))).setData(op.key)
        menu.addSeparator()
        must(menu.addAction(tr("cancel_operation_all"))).setData(ALL_OPERATIONS)
        chosen = menu.exec(host.cancel_btn.mapToGlobal(host.cancel_btn.rect().bottomLeft()))
        return chosen.data() if chosen is not None else None
