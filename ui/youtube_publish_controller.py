"""The YouTube publish flow: the wizard, its approved cover and the upload.

Moved out of ``MainWindow``: it opens ``YouTubePublishDialog`` for the
record on the YouTube tab, renders the approved cover through the recipe's
own "cover" step, and runs ``YouTubeUploadWorker`` — which outlives the
dialog that started it. The window stays the host: it owns the panels, the
status line and the ``WorkerRegistry`` this controller registers with.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from application.records import add_record_badges
from application.steps import STEP_REGISTRY, build_step_context
from config import get_config, save_config
from core.i18n import tr
from core.logger import get_logger
from ui.toast import show_toast

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = get_logger(__name__)

_UPLOAD_WAIT_MS = 5000


class YouTubePublishController:
    """Publish wizard + upload for the main window (see module docstring)."""

    def __init__(self, host: "MainWindow") -> None:
        self._host = host
        # The open wizard, if any; a running upload keeps reporting to it.
        self.dialog: Any = None
        # The upload worker (core/youtube_upload_worker.py); outlives the dialog.
        self.upload: Any = None
        self.upload_record_id: Optional[int] = None
        # The open upload-queue dialog and its worker (ui/youtube_queue_worker.py);
        # the worker, like the single upload, outlives the dialog.
        self.queue_dialog: Any = None
        self.queue: Any = None

    # ------------------------------------------------------------ wizard

    def open_dialog(self) -> None:
        """Hand the current YouTube package over to the publish dialog (see
        docs/archive/YOUTUBE_PUBLISH_PLAN_2026-10.ru.md). Texts come from the
        YouTube tab so the user's edits are what gets published."""
        host = self._host
        if not host.youtube_panel.has_publishable_content():
            show_toast(host, tr("yt_publish_nothing"), kind="info")
            return
        from application.youtube_publish import DRAFT_FILE, find_video_source
        from core.paths import artifact_dir, output_dir
        from ui.youtube_publish_dialog import YouTubePublishDialog

        record_id, source_path = host.youtube_panel.provenance()
        # The wizard's cover step drives the Cover workspace: make sure it
        # holds this record's saved cover setup (a no-op when it already does).
        host.cover_view.set_provenance(record_id, source_path)
        stem = Path(source_path).stem if source_path else ""
        art_dir = (
            artifact_dir(record_id, source_path or stem or "youtube")
            if record_id is not None else None
        )
        cover_path = None
        if art_dir is not None and (art_dir / "cover.png").is_file():
            cover_path = art_dir / "cover.png"
        record_path = art_dir / "youtube_upload.json" if art_dir else None
        pending_path = art_dir / "youtube_upload.pending.json" if art_dir else None
        dialog = YouTubePublishDialog(
            host.youtube_panel.publish_texts(),
            video_path=find_video_source(source_path),
            cover_path=cover_path,
            source_name=stem or "youtube",
            save_dir=output_dir(),
            upload_enabled=art_dir is not None and self.upload_ready(),
            record_path=record_path,
            pending_path=pending_path,
            cover_studio=host.cover_view,
            host_name=get_config().cover_host_name,
            draft_path=art_dir / DRAFT_FILE if art_dir else None,
            queue_dir=art_dir,
            record_id=record_id,
            parent=host,
        )
        dialog.cover_render_requested.connect(
            lambda: self.render_cover(dialog, record_id, source_path, art_dir)
        )
        dialog.upload_requested.connect(
            lambda pkg: self.start_upload(pkg, record_id, record_path, pending_path)
        )
        dialog.upload_cancel_requested.connect(self.cancel_upload)
        dialog.queue_run_requested.connect(lambda: self._upload_queued_now(dialog))
        self.dialog = dialog
        try:
            dialog.exec()
        finally:
            # A running upload keeps going without the dialog; its signals
            # then just stop reaching it.
            self.dialog = None
            dialog.deleteLater()

    def _upload_queued_now(self, dialog) -> None:
        """"Upload now" on the wizard's last step: the package is approved
        and queued, so the wizard's job is done — close it (a modal wizard
        would block every other window) and run the queue in its own,
        non-modal window."""
        from PyQt6.QtCore import QTimer

        dialog.accept()
        # Once the wizard's exec() has returned and nothing is modal.
        QTimer.singleShot(0, lambda: self.open_queue(start=True))

    def render_cover(self, dialog, record_id, source_path, art_dir) -> None:
        """The publish wizard approved its cover: render it through the
        recipe's own "cover" step (same PNG + provenance manifest, so the
        next recipe run reuses it rather than redrawing) on a worker."""
        from core.cover_worker import CoverWorker

        host = self._host
        result = host._current_result
        if art_dir is None or result is None:
            dialog.set_cover_failed(tr("yt_publish_nothing"))
            return
        name = dialog.host_name()
        cfg = get_config()
        if name and name != cfg.cover_host_name:
            # Remembered as the default host for the next episodes.
            cfg.cover_host_name = name
            save_config()
        context = build_step_context(
            source_path or "",
            result,
            record_id,
            out_dir=art_dir,
            params=host.cover_view.render_params(),
        )
        runner = STEP_REGISTRY["cover"].make_runner(context)
        worker = CoverWorker(lambda **_ignored: runner(), parent=host)

        def done(payload) -> None:
            if self.dialog is dialog:
                dialog.set_cover_ready(Path(payload["path"]))

        def failed(message: str) -> None:
            if self.dialog is dialog:
                dialog.set_cover_failed(message)

        worker.result.connect(done)
        worker.error.connect(failed)
        host._registry.register(worker, name=f"publish_cover_{id(worker)}")
        worker.start()

    # ------------------------------------------------------------ upload

    def upload_ready(self) -> bool:
        """API mode is on, the user's OAuth client is imported and the
        account is connected."""
        from core import youtube_oauth
        cfg = get_config()
        return (
            cfg.yt_publish_mode == "api"
            and bool(cfg.yt_oauth_client_id and cfg.yt_oauth_client_secret)
            and youtube_oauth.is_connected()
        )

    def start_upload(self, pkg, record_id, record_path, pending_path) -> None:
        """PublishDialog.upload_requested: run the upload worker. One
        upload at a time; the worker outlives the dialog."""
        from core.youtube_upload_worker import YouTubeUploadWorker
        dialog = self.dialog
        if self.upload is not None and self.upload.isRunning():
            if dialog is not None:
                dialog.set_upload_failed(tr("yt_publish_busy"))
            return
        if record_path is None or pending_path is None or not self.upload_ready():
            if dialog is not None:
                dialog.set_upload_failed(tr("yt_publish_relogin"))
            return
        cfg = get_config()
        self.upload_record_id = record_id
        worker = YouTubeUploadWorker(
            pkg, client_id=cfg.yt_oauth_client_id, client_secret=cfg.yt_oauth_client_secret,
            pending_path=pending_path, record_path=record_path, parent=self._host,
        )
        worker.progress.connect(self._on_progress)
        worker.thumbnail_warning.connect(self._on_cover_warning)
        worker.uploaded.connect(self._on_uploaded)
        worker.cancelled.connect(self._on_cancelled)
        worker.failed.connect(self._on_failed)
        self._host._registry.register(worker, name="youtube_upload")
        self.upload = worker
        worker.start()

    def cancel_upload(self) -> None:
        if self.upload is not None and self.upload.isRunning():
            self.upload.cancel()

    def shutdown(self) -> None:
        """Shutdownable (ui/shutdownable.py): cancel a running upload (or
        queue run) and wait a bounded time; one that outlives it is
        retired, not dropped."""
        for worker in (self.upload, self.queue):
            if worker is None or not worker.isRunning():
                continue
            worker.cancel()
            if not worker.wait(_UPLOAD_WAIT_MS):
                logger.warning(
                    "YouTube upload did not stop within %d ms; deferring cleanup to WorkerRegistry",
                    _UPLOAD_WAIT_MS,
                )
                self._host._registry.retire(worker)

    # ------------------------------------------------------------ queue

    def queue_connected(self) -> bool:
        """The OAuth client is imported and an account is signed in — all
        the queue needs (it is an explicit run, so API mode is not asked)."""
        from core import youtube_oauth
        cfg = get_config()
        return bool(cfg.yt_oauth_client_id and cfg.yt_oauth_client_secret) and \
            youtube_oauth.is_connected()

    def queue_running(self) -> bool:
        return self.queue is not None and self.queue.isRunning()

    def open_queue(self, start: bool = False) -> None:
        """Show the upload queue (File → YouTube upload queue…, or "Upload
        now" on the wizard's last step, which also starts it). The window
        is not modal: an upload takes minutes, and the rest of the app
        stays usable meanwhile. An open window is brought to the front
        rather than opened twice."""
        from PyQt6.QtCore import Qt

        from core.paths import output_dir
        from ui.youtube_queue_dialog import YouTubeQueueDialog

        dialog = self.queue_dialog
        if dialog is None:
            dialog = YouTubeQueueDialog(
                output_dir(), connected=self.queue_connected(), running=self.queue_running(),
                parent=self._host,
            )
            dialog.setModal(False)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.run_requested.connect(self.start_queue)
            dialog.cancel_requested.connect(self.cancel_queue)
            dialog.settings_requested.connect(lambda: self._queue_settings(dialog))
            dialog.finished.connect(lambda _result: self._on_queue_closed(dialog))
            self.queue_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        if start and not self.queue_running():
            self.start_queue(False)

    def _on_queue_closed(self, dialog) -> None:
        # A running upload goes on; its results then come as a toast.
        if self.queue_dialog is dialog:
            self.queue_dialog = None

    def _queue_settings(self, dialog) -> None:
        self._host._open_settings("settings_category_ai")
        dialog.set_connected(self.queue_connected())

    def start_queue(self, update: bool = False) -> None:
        """Run the queue on a worker: upload what is waiting, or with
        *update* push the queued texts to the videos already uploaded."""
        from core.paths import output_dir
        from ui.youtube_queue_worker import YouTubeQueueWorker

        dialog = self.queue_dialog
        busy = self.queue_running() or (self.upload is not None and self.upload.isRunning())
        if busy:
            if dialog is not None:
                dialog.set_error(tr("yt_publish_busy"))
            return
        if not self.queue_connected():
            if dialog is not None:
                dialog.set_connected(False)
            return
        cfg = get_config()
        worker = YouTubeQueueWorker(
            output_dir(), client_id=cfg.yt_oauth_client_id,
            client_secret=cfg.yt_oauth_client_secret, update=update, parent=self._host,
        )
        worker.progress.connect(self._on_queue_progress)
        worker.uploaded.connect(self._on_queue_uploaded)
        worker.done.connect(self._on_queue_done)
        worker.failed.connect(self._on_queue_failed)
        self._host._registry.register(worker, name="youtube_queue")
        self.queue = worker
        if dialog is not None:
            dialog.set_running(True, update=update)
        worker.start()

    def cancel_queue(self) -> None:
        if self.queue_running():
            self.queue.cancel()

    def _on_queue_progress(self, item, percent: int) -> None:
        self._host.status_label.setText(tr("status_yt_uploading", percent=percent))
        if self.queue_dialog is not None:
            self.queue_dialog.set_progress(item, percent)

    def _on_queue_uploaded(self, item, _record) -> None:
        if add_record_badges(item.record_id, {"youtube_upload"}):
            self._host.library_view.refresh()

    def _on_queue_done(self, outcomes) -> None:
        host = self._host
        host.status_label.setText("")
        sent = sum(1 for outcome in outcomes if outcome.status in ("uploaded", "updated"))
        failed = [outcome for outcome in outcomes if outcome.status == "failed"]
        if self.queue_dialog is not None:
            self.queue_dialog.set_outcomes(outcomes)
        elif failed:
            detail = tr("yt_publish_relogin") if any(o.relogin for o in failed) else failed[0].detail
            show_toast(host, tr("yt_publish_upload_failed", detail=detail), kind="error")
        elif sent:
            show_toast(host, tr("yt_queue_result", sent=sent), kind="success")

    def _on_queue_failed(self, message: str) -> None:
        self._host.status_label.setText("")
        if self.queue_dialog is not None:
            self.queue_dialog.set_error(message)
        else:
            show_toast(self._host, tr("yt_queue_run_failed", detail=message), kind="error")

    def _on_progress(self, percent: int, sent: object, total: object) -> None:
        self._host.status_label.setText(tr("status_yt_uploading", percent=percent))
        if self.dialog is not None:
            self.dialog.set_upload_progress(percent, sent, total)

    def _on_cover_warning(self, message: str) -> None:
        if self.dialog is not None:
            self.dialog.set_thumbnail_warning(message)
        else:
            show_toast(self._host, tr("yt_publish_cover_warning", detail=message), kind="warning")

    def _on_uploaded(self, record) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from ui.youtube_publish_dialog import studio_edit_url
        host = self._host
        host.status_label.setText(tr("toast_yt_uploaded"))
        show_toast(host, tr("toast_yt_uploaded"), kind="success")
        if self.dialog is not None:
            self.dialog.set_upload_done(record)
        QDesktopServices.openUrl(QUrl(studio_edit_url(record.video_id)))
        if add_record_badges(self.upload_record_id, {"youtube_upload"}):
            host.library_view.refresh()

    def _on_cancelled(self) -> None:
        self._host.status_label.setText("")
        if self.dialog is not None:
            self.dialog.set_upload_cancelled()

    def _on_failed(self, message: str, relogin: bool) -> None:
        self._host.status_label.setText("")
        text = tr("yt_publish_relogin") if relogin else message
        show_toast(self._host, tr("yt_publish_upload_failed", detail=text), kind="error")
        if self.dialog is not None:
            self.dialog.set_upload_failed(text)
