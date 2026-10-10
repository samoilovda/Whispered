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
        self.dialog = dialog
        try:
            dialog.exec()
        finally:
            # A running upload keeps going without the dialog; its signals
            # then just stop reaching it.
            self.dialog = None
            dialog.deleteLater()

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
        """Shutdownable (ui/shutdownable.py): cancel a running upload and
        wait a bounded time; one that outlives it is retired, not dropped."""
        worker = self.upload
        if worker is None or not worker.isRunning():
            return
        worker.cancel()
        if not worker.wait(_UPLOAD_WAIT_MS):
            logger.warning(
                "YouTube upload did not stop within %d ms; deferring cleanup to WorkerRegistry",
                _UPLOAD_WAIT_MS,
            )
            self._host._registry.retire(worker)

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
