"""
Whispered UI - YouTube upload queue.

The in-app face of ``tools/youtube_autoupload.py``: lists the records
queued on the publish wizard's last step with where each one stands, and
uploads them ("Upload queue") or pushes their edited texts to the videos
already on YouTube ("Update texts on YouTube"). The dialog is only a view:
``YouTubePublishController`` runs ``YouTubeQueueWorker`` and reports back
through ``set_running`` / ``set_progress`` / ``set_outcomes`` /
``set_error``, so an upload keeps going if the dialog is closed.

Not modal (an upload takes minutes and the rest of the app stays usable),
but rebuilt each time it is opened, like a transient window: its captions
follow a language change on the next opening, not live.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from application.youtube_autoupload import (
    UPLOADS_PER_DAY, Outcome, QueuedUpload, find_queued, problems, uploaded_record,
)
from core.i18n import tr, tr_count
from ui.theme import set_role
from ui.youtube_publish_dialog import studio_edit_url

_COL_TITLE, _COL_PRIVACY, _COL_STATUS = range(3)
_VIDEO_ID_ROLE = Qt.ItemDataRole.UserRole


class YouTubeQueueDialog(QDialog):
    """The upload queue under *root* (the app's output folder)."""

    run_requested = pyqtSignal(bool)     # update: push texts instead of uploading
    cancel_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    connect_requested = pyqtSignal()

    def __init__(self, root: Path, *, connected: bool, running: bool = False,
                 client_configured: bool = True,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("yt_queue_title"))
        self.setMinimumSize(720, 360)
        self._root = root
        self._connected = connected
        self._running = running
        # With the OAuth client imported, signing in again happens right
        # here; without one the user is sent to Settings to import it.
        self._client_configured = client_configured
        self._connecting = False
        self._rows: dict[Path, QTreeWidgetItem] = {}
        self._items: list[QueuedUpload] = []
        layout = QVBoxLayout(self)
        intro = QLabel(tr("yt_queue_intro", n=UPLOADS_PER_DAY))
        intro.setWordWrap(True)
        set_role(intro, "dim")
        layout.addWidget(intro)

        self._tree = QTreeWidget()
        self._tree.setRootIsDecorated(False)
        self._tree.setHeaderLabels([tr("yt_publish_label_title"), tr("yt_publish_privacy"),
                                    tr("yt_queue_status")])
        header = self._tree.header()
        if header is not None:
            header.setSectionResizeMode(_COL_TITLE, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(_COL_PRIVACY, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(_COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.itemDoubleClicked.connect(self._open_in_studio)
        layout.addWidget(self._tree, 1)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)
        self._message = QLabel()
        self._message.setWordWrap(True)
        self._message.setVisible(False)
        layout.addWidget(self._message)

        buttons = QHBoxLayout()
        self._settings_btn = QPushButton(tr("yt_queue_settings"))
        self._settings_btn.clicked.connect(self.settings_requested.emit)
        buttons.addWidget(self._settings_btn)
        self._connect_btn = QPushButton(tr("yt_queue_connect"))
        self._connect_btn.setToolTip(tr("yt_queue_connect_tip"))
        self._connect_btn.clicked.connect(self._request_connect)
        buttons.addWidget(self._connect_btn)
        buttons.addStretch(1)
        self._update_btn = QPushButton(tr("yt_queue_update"))
        self._update_btn.setToolTip(tr("yt_queue_update_tip"))
        self._update_btn.clicked.connect(lambda: self._request(True))
        buttons.addWidget(self._update_btn)
        self._cancel_btn = QPushButton(tr("btn_cancel"))
        self._cancel_btn.setProperty("variant", "danger")
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)
        buttons.addWidget(self._cancel_btn)
        close_btn = QPushButton(tr("yt_publish_close"))
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        self._upload_btn = QPushButton(tr("yt_queue_upload"))
        self._upload_btn.setProperty("variant", "primary")
        self._upload_btn.clicked.connect(lambda: self._request(False))
        buttons.addWidget(self._upload_btn)
        layout.addLayout(buttons)

        self.reload()
        if not connected:
            self._show_message(tr("yt_queue_not_connected" if client_configured
                                  else "yt_queue_no_client"), error=True)

    # ------------------------------------------------------------ state

    def reload(self) -> None:
        """Re-read the queue from disk and show where each record stands."""
        self._tree.clear()
        self._rows.clear()
        from application.records import live_record_ids

        self._items = find_queued(self._root, live_record_ids())
        for item in self._items:
            row = QTreeWidgetItem([item.package.title, _privacy_label(item.package.privacy), ""])
            row.setToolTip(_COL_TITLE, str(item.package.video_path))
            self._tree.addTopLevelItem(row)
            self._rows[item.artifact_dir] = row
            done = uploaded_record(item)
            if done is not None:
                self._set_status(row, tr("yt_queue_state_uploaded"), done.video_id)
            else:
                issues = problems(item)
                self._set_status(row, "; ".join(issues) if issues else tr("yt_queue_state_waiting"))
        if not self._items:
            self._show_message(tr("yt_queue_empty"))
        self._refresh_buttons()

    def waiting_count(self) -> int:
        return sum(1 for item in self._items
                   if uploaded_record(item) is None and not problems(item))

    def _refresh_buttons(self) -> None:
        running = self._running
        waiting = self.waiting_count()
        uploaded = len(self._items) - waiting
        self._upload_btn.setEnabled(self._connected and not running and waiting > 0)
        self._upload_btn.setText(
            tr_count("yt_queue_upload_n", waiting) if waiting else tr("yt_queue_upload"))
        self._update_btn.setEnabled(self._connected and not running and uploaded > 0)
        self._cancel_btn.setVisible(running)
        self._settings_btn.setVisible(not self._connected and not self._client_configured)
        self._connect_btn.setVisible(not self._connected and self._client_configured)
        self._connect_btn.setEnabled(not self._connecting and not running)
        self._connect_btn.setText(
            tr("yt_queue_connecting") if self._connecting else tr("yt_queue_connect"))
        self._progress.setVisible(running)

    def _request(self, update: bool) -> None:
        self._message.setVisible(False)
        self.run_requested.emit(update)

    def _request_connect(self) -> None:
        self._message.setVisible(False)
        self.connect_requested.emit()

    def set_connected(self, connected: bool, *, client_configured: Optional[bool] = None) -> None:
        self._connected = connected
        if client_configured is not None:
            self._client_configured = client_configured
        if connected:
            self._message.setVisible(False)
        self._refresh_buttons()

    def set_connecting(self, connecting: bool) -> None:
        """The browser sign-in started from this window is running."""
        self._connecting = connecting
        if connecting:
            self._show_message(tr("yt_queue_connect_wait"))
        self._refresh_buttons()

    def set_login_result(self, channel: Optional[str], error: str = "") -> None:
        """The sign-in ended: *channel* is set on success ("" when its name
        could not be read), None when it was cancelled or failed."""
        self._connecting = False
        if channel is not None:
            self._connected = True
            self._show_message(tr("yt_queue_connected", channel=channel or "\u2014"))
        elif error:
            self._show_message(tr("settings_yt_connect_failed", detail=error), error=True)
        else:
            self._message.setVisible(False)
        self._refresh_buttons()

    def set_running(self, running: bool, *, update: bool = False) -> None:
        self._running = running
        if running:
            self._progress.setValue(0)
            for item in self._items:
                if update and uploaded_record(item) is not None:
                    self._set_status(self._rows[item.artifact_dir], tr("yt_queue_state_updating"))
        self._refresh_buttons()

    def set_progress(self, item: QueuedUpload, percent: int) -> None:
        row = self._rows.get(item.artifact_dir)
        if row is not None:
            self._set_status(row, tr("yt_queue_state_uploading", percent=percent))
        self._progress.setValue(percent)

    def set_outcomes(self, outcomes: list[Outcome]) -> None:
        """A run finished: each record's result, then a one-line summary."""
        self._running = False
        relogin = False
        for outcome in outcomes:
            row = self._rows.get(outcome.item.artifact_dir)
            if row is None:
                continue
            if outcome.status == "uploaded":
                self._set_status(row, tr("yt_queue_state_done"), outcome.video_id)
            elif outcome.status == "updated":
                self._set_status(row, tr("yt_queue_state_updated"), outcome.video_id)
            elif outcome.status == "cancelled":
                self._set_status(row, tr("yt_queue_state_cancelled"))
            elif outcome.status == "failed":
                relogin = relogin or outcome.relogin
                self._set_status(row, tr("yt_queue_state_failed", detail=outcome.detail),
                                 outcome.video_id)
            elif outcome.status == "skipped" and outcome.video_id:
                self._set_status(row, tr("yt_queue_state_uploaded"), outcome.video_id)
            elif outcome.status == "skipped":
                self._set_status(row, outcome.detail)
        if relogin:
            self._connected = False
            self._show_message(tr("yt_queue_relogin"), error=True)
        else:
            sent = sum(1 for o in outcomes if o.status in ("uploaded", "updated"))
            failed = sum(1 for o in outcomes if o.status == "failed")
            key = "yt_queue_result_failed" if failed else "yt_queue_result"
            self._show_message(tr(key, sent=sent, failed=failed), error=bool(failed))
        self._refresh_buttons()

    def set_error(self, message: str) -> None:
        self._running = False
        self._show_message(tr("yt_queue_run_failed", detail=message), error=True)
        self._refresh_buttons()

    # ------------------------------------------------------------ helpers

    def _set_status(self, row: QTreeWidgetItem, text: str, video_id: str = "") -> None:
        row.setText(_COL_STATUS, text)
        row.setToolTip(_COL_STATUS, text)
        row.setData(_COL_TITLE, _VIDEO_ID_ROLE, video_id or None)
        if video_id:
            row.setToolTip(_COL_TITLE, tr("yt_queue_open_studio_tip"))

    def _show_message(self, text: str, *, error: bool = False) -> None:
        self._message.setText(text)
        set_role(self._message, "warning-text" if error else "dim")
        self._message.setVisible(bool(text))

    def _open_in_studio(self, row: QTreeWidgetItem, _column: int) -> None:
        video_id = row.data(_COL_TITLE, _VIDEO_ID_ROLE)
        if video_id:
            QDesktopServices.openUrl(QUrl(studio_edit_url(str(video_id))))


def _privacy_label(privacy: str) -> str:
    return tr("yt_publish_unlisted") if privacy == "unlisted" else tr("yt_publish_private")
