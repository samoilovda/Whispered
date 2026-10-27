"""Leaving or deleting the open record: the header's delete (the record
alone, or with the files Whispered made for it), the Library's and
Settings' deletes reaching an open record, and the clean-up that follows.
Saving edits and the pending-edit guard (_prepare_leave) still live on
MainWindow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QMessageBox

from core.i18n import tr
from core.logger import get_logger
from ui.project_dialogs import human_size
from ui.toast import show_toast

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = get_logger(__name__)


class DocumentLifecycleController:
    def __init__(self, host: "MainWindow") -> None:
        self._host = host

    def delete_open_record(self) -> None:
        """Ask what to delete (UX-16): the record alone, or the record and
        the files Whispered generated for it — with their size, measured in
        the background first. Source media is never touched."""
        host = self._host
        record_id = host._last_record_id
        if record_id is None:
            return
        from core.disk_usage import DiskUsageWorker
        from core.paths import artifact_dir

        folder = str(artifact_dir(record_id, host._artifact_source()))
        worker = DiskUsageWorker(folder, host)
        worker.measured.connect(
            lambda path, size, files, r=record_id: self.confirm_delete(r, path, size, files))
        worker.failed.connect(
            lambda _msg, r=record_id, p=folder: self.confirm_delete(r, p, 0, 0))
        host._registry.register(worker, name=f"disk_usage_{id(worker)}")
        worker.start()

    def confirm_delete(self, record_id: int, folder: str, size: int, files: int) -> None:
        host = self._host
        if record_id != host._last_record_id:
            return
        box = QMessageBox(host)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("history_delete_title"))
        box.setText(tr("history_delete_msg"))
        record_only = box.addButton(tr("history_delete_record_only"),
                                    QMessageBox.ButtonRole.AcceptRole)
        with_files = None
        if files:
            box.setInformativeText(tr("history_delete_files_info", count=files,
                                      size=human_size(size), path=folder))
            with_files = box.addButton(tr("history_delete_with_files", size=human_size(size)),
                                       QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        choice = self.ask_delete(box)
        if choice is None or choice not in (record_only, with_files):
            return
        # The record is going: an open edit or draft has nothing to save to.
        if host.transcript_view.is_editing():
            host.transcript_view._exit_edit_mode(save=False)
        try:
            from core.history import get_history_store
            get_history_store().delete(record_id)
        except Exception as exc:
            logger.warning("Failed to delete record %s: %s", record_id, exc)
            return
        if choice is with_files and with_files is not None:
            from PyQt6.QtCore import QFile

            # To the Trash, not erased: a mistaken click stays recoverable.
            if not QFile.moveToTrash(folder):
                show_toast(host, tr("history_delete_files_failed", path=folder), kind="warning")
        self.close_open_record()

    def close_open_record(self) -> None:
        """The open record no longer exists: leave it and show a new draft."""
        host = self._host
        host._last_record_id = None
        host._last_persist_ok = True
        host._listen = None
        host._listen_queue = []
        # Single-step jobs for the deleted record must not land anywhere.
        host._cancel_youtube_job()
        host._cancel_insights_job()
        host._cancel_clean_job()
        host._cancel_article_job()
        host._cancel_book_job()
        for panel in (host.transcript_view, host.cleaned_view, host.article_view, host.youtube_panel,
                      host.insights_panel, host.subtitle_view, host.clips_view, host.cut_view):
            panel.clear()
        host.book_panel.clear_book()
        host.chat_panel.clear_transcript()
        host._cleaned_text = None
        host._load_bookmarks()
        host.library_view.set_open_record(None)
        host.library_view.refresh()
        host._show_new_draft()

    def on_records_deleted(self, record_ids: list) -> None:
        """The Library or Settings deleted records (an empty list = all):
        if the open one went with them, close it like the record header's
        own delete does."""
        host = self._host
        rid = host._last_record_id
        if rid is None:
            return
        if record_ids:
            gone = rid in record_ids
        else:
            from core.history import get_history_store
            try:
                gone = get_history_store().get_record(rid) is None
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not check record %s: %s", rid, exc)
                return
        if not gone:
            return
        if host.transcript_view.is_editing():
            host.transcript_view._exit_edit_mode(save=False)
        self.close_open_record()

    def delete_blocked(self, record_id: int | None) -> bool:
        """A recipe run is writing into this record (None = any record)."""
        host = self._host
        run = host.recipe_run
        return run.is_running() and (record_id is None or run.record_id == record_id)

    def ask_delete(self, box: QMessageBox):
        box.exec()
        return box.clickedButton()
