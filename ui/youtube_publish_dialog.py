"""
Whispered UI - YouTube publish dialog.

Shown after a "Video for YouTube" run (or from the YouTube tab): the final
title / description / tags / cover, validated against YouTube's limits, with
hand-off actions — copy each field, show the video in the file manager,
save everything as a folder, open YouTube Studio's upload page. Nothing is
sent over the network from here.

Opened with ``.exec()`` and rebuilt each time, so (per CLAUDE.md's i18n
notes) it is deliberately not retranslated live.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from application.youtube_publish import (
    DESCRIPTION_MAX_BYTES, TAGS_MAX_CHARS, THUMBNAIL_MAX_BYTES, TITLE_MAX_CHARS, build_package,
    description_bytes, fit_tags, normalize_titles, parse_tags, tags_length,
    validate_package,
)
from core.i18n import tr
from core.logger import get_logger
from core.platform_support import reveal_in_file_manager
from domain.youtube_publish import PublishPackage, UploadRecord
from ui.theme import set_role
from ui.toast import show_toast

logger = get_logger(__name__)

STUDIO_UPLOAD_URL = "https://www.youtube.com/upload"
STUDIO_EDIT_URL = "https://studio.youtube.com/video/{video_id}/edit"


def studio_edit_url(video_id: str) -> str:
    return STUDIO_EDIT_URL.format(video_id=video_id)


class YouTubePublishDialog(QDialog):
    """Hand-off dialog for one finished YouTube package.

    *texts* is ``YouTubePanel.publish_texts()``; *video_path* the source
    video when known (``None`` leaves the user to choose one); *cover_path*
    the rendered ``cover.png``; *save_dir* where "Save package" writes its
    ``<source_name>_youtube`` folder.

    With *upload_enabled* (API mode, account connected) it also offers
    "Upload to YouTube": the dialog only validates and emits
    ``upload_requested(PublishPackage)``; the owner runs the upload worker
    (so closing the dialog does not stop it) and reports back through
    ``set_upload_progress`` / ``set_upload_done`` / ``set_upload_failed`` /
    ``set_upload_cancelled``. *record_path* / *pending_path* are the
    artifact-dir files of a finished / interrupted upload of this record.
    """

    upload_requested = pyqtSignal(object)
    upload_cancel_requested = pyqtSignal()

    def __init__(
        self,
        texts: dict,
        *,
        video_path: Optional[Path] = None,
        cover_path: Optional[Path] = None,
        source_name: str = "",
        save_dir: Optional[Path] = None,
        upload_enabled: bool = False,
        record_path: Optional[Path] = None,
        pending_path: Optional[Path] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("yt_publish_title"))
        self.setMinimumWidth(560)
        self._video_path = video_path
        self._cover_path = cover_path
        self._source_name = source_name or "youtube"
        self._save_dir = save_dir
        self._upload_enabled = upload_enabled
        self._record_path = record_path
        self._pending_path = pending_path
        self._uploading = False
        self._language: Optional[str] = texts.get("language")
        self._chapter_check = texts.get("chapter_check")
        self._build_ui(texts)
        self._refresh()

    # ------------------------------------------------------------------ UI

    def _build_ui(self, texts: dict) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        intro = QLabel(tr("yt_publish_intro"))
        intro.setWordWrap(True)
        set_role(intro, "dim")
        layout.addWidget(intro)

        layout.addLayout(self._video_row())

        self._title_combo = QComboBox()
        self._title_combo.setEditable(True)
        self._title_combo.addItems(normalize_titles(texts.get("titles")))
        self._title_combo.currentTextChanged.connect(self._refresh)
        self._title_counter = QLabel()
        layout.addLayout(self._field_row(
            tr("yt_publish_label_title"), self._title_combo, self._title_counter,
            tr("yt_publish_copy_title"), self._copy_title,
        ))

        self._desc_edit = QPlainTextEdit(texts.get("description") or "")
        self._desc_edit.setMinimumHeight(140)
        self._desc_edit.textChanged.connect(self._refresh)
        self._desc_counter = QLabel()
        layout.addLayout(self._field_row(
            tr("yt_publish_label_description"), self._desc_edit, self._desc_counter,
            tr("yt_publish_copy_description"), self._copy_description,
        ))

        self._tags_edit = QLineEdit(", ".join(parse_tags(texts.get("tags"))))
        self._tags_edit.textChanged.connect(self._refresh)
        self._tags_counter = QLabel()
        layout.addLayout(self._field_row(
            tr("yt_publish_label_tags"), self._tags_edit, self._tags_counter,
            tr("yt_publish_copy_tags"), self._copy_tags,
        ))

        layout.addLayout(self._cover_row())

        self._issues_label = QLabel()
        self._issues_label.setWordWrap(True)
        self._issues_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._issues_label)

        if self._upload_enabled:
            layout.addLayout(self._upload_row())

        actions = QHBoxLayout()
        self._reveal_btn = QPushButton(tr("yt_publish_reveal"))
        self._reveal_btn.clicked.connect(self._reveal_video)
        actions.addWidget(self._reveal_btn)
        self._save_btn = QPushButton(tr("yt_publish_save_package"))
        self._save_btn.clicked.connect(self._save_package)
        actions.addWidget(self._save_btn)
        actions.addStretch(1)
        self._studio_btn = QPushButton(tr("yt_publish_open_studio"))
        if not self._upload_enabled:
            self._studio_btn.setProperty("variant", "primary")
        self._studio_btn.clicked.connect(self._open_studio)
        actions.addWidget(self._studio_btn)
        close_btn = QPushButton(tr("yt_publish_close"))
        close_btn.clicked.connect(self.reject)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

    def _upload_row(self) -> QVBoxLayout:
        box = QVBoxLayout()
        hint = QLabel(tr("yt_publish_unverified_hint"))
        hint.setWordWrap(True)
        set_role(hint, "dim")
        box.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("yt_publish_privacy")))
        self._privacy_combo = QComboBox()
        self._privacy_combo.addItem(tr("yt_publish_private"), "private")
        self._privacy_combo.addItem(tr("yt_publish_unlisted"), "unlisted")
        row.addWidget(self._privacy_combo)
        row.addStretch(1)
        self._cancel_upload_btn = QPushButton(tr("btn_cancel"))
        self._cancel_upload_btn.setProperty("variant", "danger")
        self._cancel_upload_btn.setVisible(False)
        self._cancel_upload_btn.clicked.connect(self.upload_cancel_requested.emit)
        row.addWidget(self._cancel_upload_btn)
        self._upload_btn = QPushButton()
        self._upload_btn.setProperty("variant", "primary")
        self._upload_btn.clicked.connect(self._start_upload)
        row.addWidget(self._upload_btn)
        box.addLayout(row)
        self._upload_progress = QProgressBar()
        self._upload_progress.setRange(0, 100)
        self._upload_progress.setVisible(False)
        box.addWidget(self._upload_progress)
        self._upload_status = QLabel()
        self._upload_status.setWordWrap(True)
        self._upload_status.setVisible(False)
        box.addWidget(self._upload_status)
        return box

    def _video_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("yt_publish_video")))
        self._video_label = QLabel()
        self._video_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self._video_label, 1)
        choose = QPushButton(tr("yt_publish_choose_video"))
        choose.clicked.connect(self._choose_video)
        row.addWidget(choose)
        return row

    def _cover_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("yt_publish_label_cover")))
        self._cover_label = QLabel()
        pixmap = QPixmap(str(self._cover_path)) if self._cover_path else QPixmap()
        if pixmap.isNull():
            self._cover_label.setText(tr("yt_publish_no_cover"))
            set_role(self._cover_label, "dim")
        else:
            self._cover_label.setPixmap(pixmap.scaledToWidth(
                200, Qt.TransformationMode.SmoothTransformation))
        row.addWidget(self._cover_label, 1)
        return row

    def _field_row(self, caption: str, widget: QWidget, counter: QLabel,
                   copy_text: str, copy_slot) -> QVBoxLayout:
        box = QVBoxLayout()
        head = QHBoxLayout()
        head.addWidget(QLabel(caption))
        head.addStretch(1)
        set_role(counter, "dim")
        head.addWidget(counter)
        copy_btn = QPushButton(copy_text)
        copy_btn.setProperty("variant", "ghost")
        copy_btn.clicked.connect(copy_slot)
        head.addWidget(copy_btn)
        box.addLayout(head)
        box.addWidget(widget)
        return box

    # --------------------------------------------------------------- state

    def current_title(self) -> str:
        return self._title_combo.currentText().strip()

    def current_tags(self) -> list[str]:
        return parse_tags(self._tags_edit.text())

    def package(self) -> PublishPackage:
        """The package as currently edited in the dialog."""
        return build_package(
            video_path=self._video_path or Path(),
            titles=[self.current_title()],
            description=self._desc_edit.toPlainText(),
            tags=self.current_tags(),
            cover_path=self._cover_path,
            language=self._language,
        )

    def _counter_text(self, count: int, limit: int) -> str:
        return tr("yt_publish_counter", count=count, limit=limit)

    def _refresh(self, *_: object) -> None:
        self._video_label.setText(
            str(self._video_path) if self._video_path else tr("yt_publish_no_video"))
        self._title_counter.setText(
            self._counter_text(len(self.current_title()), TITLE_MAX_CHARS))
        self._desc_counter.setText(self._counter_text(
            description_bytes(self._desc_edit.toPlainText()), DESCRIPTION_MAX_BYTES))
        self._tags_counter.setText(
            self._counter_text(tags_length(fit_tags(self.current_tags())[0]), TAGS_MAX_CHARS))
        self._reveal_btn.setEnabled(bool(self._video_path and self._video_path.is_file()))

        lines = []
        if self._video_path is None:
            lines.append(tr("yt_publish_issue_video_missing"))
        for issue in validate_package(self.package()):
            if self._video_path is None and issue.kind == "video_missing":
                continue
            lines.append(tr(issue.key, **issue.format_params()))
        check = self._chapter_check
        if check is not None and not check.shows_on_youtube:
            lines.append(tr("yt_publish_chapters_warning"))
        self._issues_label.setText("\n".join(f"• {line}" for line in lines))
        self._issues_label.setVisible(bool(lines))
        set_role(self._issues_label, "warning-text")
        if self._upload_enabled:
            self._refresh_upload_button()

    # -------------------------------------------------------------- actions

    def _copy(self, text: str) -> None:
        if not text:
            return
        QApplication.clipboard().setText(text)
        show_toast(self, tr("yt_publish_copied"), kind="success")

    def _copy_title(self) -> None:
        self._copy(self.current_title())

    def _copy_description(self) -> None:
        self._copy(self._desc_edit.toPlainText())

    def _copy_tags(self) -> None:
        self._copy(", ".join(fit_tags(self.current_tags())[0]))

    def _choose_video(self) -> None:
        start = str(self._video_path.parent) if self._video_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("yt_publish_choose_video_title"), start,
            "Video (*.mp4 *.mkv *.avi *.mov *.webm *.wmv *.flv *.m4v);;All Files (*)",
        )
        if path:
            self._video_path = Path(path)
            self._refresh()

    def _reveal_video(self) -> None:
        if self._video_path is None:
            return
        if reveal_in_file_manager(self._video_path):
            return
        # No select-in-folder command here (Linux): open the containing folder.
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._video_path.parent))):
            show_toast(self, tr("yt_publish_reveal_failed"), kind="error")

    def _open_studio(self) -> None:
        QDesktopServices.openUrl(QUrl(STUDIO_UPLOAD_URL))

    def _save_package(self) -> None:
        """Write title/description/tags (and a copy of the cover) into
        ``<save_dir>/<source_name>_youtube/`` — handy for dragging the cover
        into Studio."""
        if self._save_dir is None:
            return
        folder = self._save_dir / f"{self._source_name}_youtube"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "title.txt").write_text(self.current_title(), encoding="utf-8")
            (folder / "description.txt").write_text(
                self._desc_edit.toPlainText(), encoding="utf-8")
            (folder / "tags.txt").write_text(
                ", ".join(fit_tags(self.current_tags())[0]), encoding="utf-8")
            if self._cover_path is not None and self._cover_path.is_file():
                (folder / "cover.png").write_bytes(self._cover_path.read_bytes())
        except OSError as exc:
            logger.warning("Failed to save YouTube package to %s: %s", folder, exc)
            show_toast(self, tr("yt_publish_save_failed"), kind="error")
            return
        show_toast(self, tr("yt_publish_saved", path=str(folder)), kind="success")

    # ------------------------------------------------------------- API upload

    def _has_pending_upload(self) -> bool:
        if self._pending_path is None or self._video_path is None:
            return False
        from core.youtube_upload import load_pending
        return load_pending(self._pending_path, self._video_path) is not None

    def _existing_record(self) -> Optional[UploadRecord]:
        if self._record_path is None:
            return None
        from core.youtube_upload import load_upload_record
        return load_upload_record(self._record_path)

    def _blocking_issues(self) -> list:
        return [issue for issue in validate_package(self.package()) if issue.blocking]

    def _refresh_upload_button(self) -> None:
        self._upload_btn.setText(
            tr("yt_publish_resume") if self._has_pending_upload() else tr("yt_publish_upload"))
        self._upload_btn.setEnabled(not self._uploading and not self._blocking_issues())

    def upload_package(self) -> PublishPackage:
        """The package as it would be uploaded: tags cut to the 500
        character budget, the chosen visibility, and a cover YouTube
        accepts (a PNG over 2 MB is re-encoded as JPEG)."""
        pkg = self.package()
        return replace(
            pkg,
            tags=tuple(fit_tags(pkg.tags)[0]),
            privacy=str(self._privacy_combo.currentData() or "private"),
            thumbnail_path=self._thumbnail_for_upload(),
        )

    def _thumbnail_for_upload(self) -> Optional[Path]:
        cover = self._cover_path
        if cover is None or not cover.is_file():
            return None
        if cover.stat().st_size <= THUMBNAIL_MAX_BYTES:
            return cover
        target_dir = self._pending_path.parent if self._pending_path else cover.parent
        target = target_dir / "cover_upload.jpg"
        image = QImage(str(cover))
        if image.isNull() or not image.save(str(target), "JPEG", 90):
            logger.warning("Could not shrink the cover for upload: %s", cover)
            return None
        return target

    def _confirm_duplicate(self, record: UploadRecord) -> bool:
        """True = upload again. "Open in Studio" and Cancel return False."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("yt_publish_duplicate_title"))
        box.setText(tr("yt_publish_duplicate_text", id=record.video_id, date=record.uploaded_at[:10]))
        again = box.addButton(tr("yt_publish_duplicate_again"), QMessageBox.ButtonRole.AcceptRole)
        studio = box.addButton(tr("yt_publish_duplicate_open"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is studio:
            QDesktopServices.openUrl(QUrl(studio_edit_url(record.video_id)))
        return clicked is again

    def _start_upload(self) -> None:
        if self._uploading or self._blocking_issues():
            return
        record = self._existing_record()
        if record is not None and not self._has_pending_upload() and not self._confirm_duplicate(record):
            return
        self._uploading = True
        self._upload_btn.setEnabled(False)
        self._privacy_combo.setEnabled(False)
        self._cancel_upload_btn.setVisible(True)
        self._upload_progress.setValue(0)
        self._upload_progress.setVisible(True)
        self._show_upload_status("", None)
        self.upload_requested.emit(self.upload_package())

    def _show_upload_status(self, text: str, role: Optional[str]) -> None:
        self._upload_status.setText(text)
        self._upload_status.setVisible(bool(text))
        if role:
            set_role(self._upload_status, role)

    def _finish_upload_ui(self) -> None:
        self._uploading = False
        self._privacy_combo.setEnabled(True)
        self._cancel_upload_btn.setVisible(False)
        self._upload_progress.setVisible(False)
        self._refresh_upload_button()

    def set_upload_progress(self, percent: int, sent: object = None, total: object = None) -> None:
        self._upload_progress.setValue(int(percent))

    def set_upload_done(self, record: UploadRecord) -> None:
        self._finish_upload_ui()
        self._show_upload_status(tr("yt_publish_uploaded", id=record.video_id), "success-text")

    def set_upload_failed(self, message: str) -> None:
        self._finish_upload_ui()
        self._show_upload_status(tr("yt_publish_upload_failed", detail=message), "danger-text")

    def set_upload_cancelled(self) -> None:
        self._finish_upload_ui()
        self._show_upload_status(tr("yt_publish_upload_cancelled"), "dim")

    def set_thumbnail_warning(self, message: str) -> None:
        show_toast(self, tr("yt_publish_cover_warning", detail=message), kind="warning")
