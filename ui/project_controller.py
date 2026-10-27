"""Export and import of a portable project from the File menu (N06).

A flow of its own (like the recipe run and the publish wizard): a preview
first, a worker for the long part, a status-bar operation with Cancel, and
a clear outcome. Importing adds a new record and never touches existing
ones; nothing is uploaded or published by either direction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QDialog, QFileDialog, QMessageBox

from application import project_archive as pa
from core.i18n import tr
from core.logger import get_logger
from ui.project_dialogs import ProjectPreviewDialog, human_size
from ui.project_worker import ProjectWorker
from ui.toast import show_toast

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = get_logger(__name__)


class ProjectController:
    def __init__(self, host: "MainWindow") -> None:
        self._host = host
        self._worker: Optional[ProjectWorker] = None
        self._status_text = ""

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    # ------------------------------------------------------------ export

    def _plan(self, worker: ProjectWorker, on_ready, on_failed) -> None:
        """Run a dry-run worker off the GUI thread with a status-bar
        operation and Cancel; *on_ready(plan)* continues the flow."""
        host = self._host
        self._worker = worker
        self._status_text = tr("project_preparing")
        host.status_bar.set_operation(self._status_text, cancel_text=tr("btn_cancel"))

        def ready(plan: object) -> None:
            self._clear()
            on_ready(plan)

        def failed(message: str) -> None:
            self._clear()
            on_failed(message)

        worker.planned.connect(ready)
        worker.failed.connect(failed)
        worker.cancelled.connect(self._clear)
        host._registry.register(worker, name="project")
        worker.start()

    def export_open_record(self) -> None:
        host = self._host
        record_id = host._last_record_id
        if record_id is None:
            show_toast(host, tr("project_needs_record"), kind="info")
            return
        if self.is_busy() or not host._prepare_leave():
            return
        self._plan(
            ProjectWorker(plan_record=record_id, parent=host),
            lambda plan: self._preview_export(record_id, plan),
            lambda message: QMessageBox.warning(host, tr("project_export_title"), message),
        )

    def _preview_export(self, record_id: int, plan: pa.ExportPlan) -> None:
        host = self._host
        lines = [
            tr("project_line_files", count=plan.counts["artifacts"] + 7,
               size=human_size(plan.total_bytes)),
            tr("project_line_history", versions=plan.counts["versions"],
               bookmarks=plan.counts["bookmarks"], chat=plan.counts["chat"], runs=plan.counts["runs"]),
            tr("project_line_stays_home"),
        ]
        if plan.skipped:
            lines.append(tr("project_line_skipped", count=len(plan.skipped)))
        media_text = ""
        if plan.media_size:
            media_text = tr("project_include_media", size=human_size(plan.media_size))
        dialog = ProjectPreviewDialog(
            tr("project_export_title"), plan.title, lines, tr("project_export_go"),
            media_text=media_text or tr("project_media_unavailable"),
            media_enabled=bool(plan.media_size), parent=host,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.include_media:
            self._plan(
                ProjectWorker(plan_record=record_id, include_media=True, parent=host),
                self._choose_export_file,
                lambda message: QMessageBox.warning(host, tr("project_export_title"), message),
            )
        else:
            self._choose_export_file(plan)

    def _choose_export_file(self, plan: pa.ExportPlan) -> None:
        host = self._host
        name = "".join(c if c.isalnum() or c in " -_" else "_" for c in plan.title).strip() or "project"
        path, _ = QFileDialog.getSaveFileName(
            host, tr("project_export_title"), f"{name}.whispered.zip", "Whispered project (*.whispered.zip)")
        if not path:
            return
        if not path.endswith(".zip"):
            path += ".whispered.zip"
        self._start(ProjectWorker(export_plan=plan, path=path, parent=host), path, export=True)

    # ------------------------------------------------------------ import

    def import_project(self) -> None:
        host = self._host
        if self.is_busy():
            return
        path, _ = QFileDialog.getOpenFileName(
            host, tr("project_import_title"), "", "Whispered project (*.whispered.zip *.zip)")
        if not path:
            return
        self._plan(
            ProjectWorker(inspect_path=path, parent=host),
            lambda plan: self._preview_import(path, plan),
            lambda message: QMessageBox.warning(
                host, tr("project_import_title"), tr("project_import_refused", error=message)),
        )

    def _preview_import(self, path: str, plan: pa.ImportPlan) -> None:
        host = self._host
        counts = plan.manifest.counts
        lines = [
            tr("project_line_files", count=len(plan.manifest.files), size=human_size(plan.total_bytes)),
            tr("project_line_history", versions=counts.get("versions", 0), bookmarks=counts.get("bookmarks", 0),
               chat=counts.get("chat", 0), runs=counts.get("runs", 0)),
            tr("project_line_media_in" if plan.manifest.media_included else "project_line_media_out"),
            tr("project_line_import_new"),
        ]
        dialog = ProjectPreviewDialog(tr("project_import_title"), plan.title or path, lines,
                                      tr("project_import_go"), parent=host)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._start(ProjectWorker(path=path, parent=host), path, export=False)

    # ------------------------------------------------------------ run

    def _start(self, worker: ProjectWorker, path: str, *, export: bool) -> None:
        host = self._host
        self._worker = worker
        label = tr("project_exporting" if export else "project_importing")
        self._status_text = label
        host.status_bar.set_operation(label, cancel_text=tr("btn_cancel"))
        worker.progress.connect(
            lambda done, total: host.status_bar.set_operation(
                label, progress=int(done * 100 / max(1, total)), cancel_text=tr("btn_cancel")))
        worker.done.connect(lambda result, e=export, p=path: self._finished(result, e, p))
        worker.failed.connect(self._failed)
        worker.cancelled.connect(self._cancelled)
        host._registry.register(worker, name="project")
        worker.start()

    def cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()

    def _clear(self) -> None:
        self._worker = None
        # Only take the status bar down if it still shows this operation's
        # text — another one (a transcription, a recipe) may have taken it over.
        if self._host.status_bar.status_label.text() == self._status_text:
            self._host.status_bar.clear()
        self._status_text = ""

    def _finished(self, result: object, export: bool, path: str) -> None:
        host = self._host
        self._clear()
        if export:
            show_toast(host, tr("project_exported", name=path.rsplit("/", 1)[-1]), kind="success")
            return
        host.library_view.refresh()
        if isinstance(result, int):
            host._open_record_view(result)
        show_toast(host, tr("project_imported"), kind="success")

    def _failed(self, message: str) -> None:
        self._clear()
        QMessageBox.warning(self._host, tr("project_title"), tr("project_failed", error=message))

    def _cancelled(self) -> None:
        self._clear()
        show_toast(self._host, tr("project_cancelled"), kind="info")

    def shutdown(self) -> None:
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        worker.cancel()
        if not worker.wait(5000):
            self._host._registry.retire(worker)
