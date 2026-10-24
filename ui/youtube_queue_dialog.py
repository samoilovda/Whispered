"""
Whispered UI - YouTube upload queue.

The in-app face of ``tools/youtube_autoupload.py``: lists the records
queued on the publish wizard's last step with where each one stands, and
uploads them ("Upload queue") or pushes their edited texts to the videos
already on YouTube ("Update texts on YouTube"). The dialog is only a view:
``YouTubePublishController`` runs ``YouTubeQueueWorker`` and reports back
through ``set_running`` / ``set_progress`` / ``set_outcomes`` /
``set_error``, so an upload keeps going if the dialog is closed.

Opened with ``.exec()`` and rebuilt each time, so (per CLAUDE.md's i18n
notes) it is deliberately not retranslated live.
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

    def __init__(self, root: Path, *, connected: bool, running: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("yt_queue_title"))
        self.setMinimumSize(720, 360)
        self._root = root
        self._connected = connected
        self._running = running
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
            self._show_message(tr("yt_queue_not_connected"), error=True)

    # ------------------------------------------------------------ state

    def reload(self) -> None:
        """Re-read the queue from disk and show where each record stands."""
        self._tree.clear()
        self._rows.clear()
        self._items = find_queued(self._root)
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
        self._settings_btn.setVisible(not self._connected)
        self._progress.setVisible(running)

    def _request(self, update: bool) -> None:
        self._message.setVisible(False)
        self.run_requested.emit(update)

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        if connected:
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
            self._show_message(tr("yt_publish_relogin"), error=True)
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
