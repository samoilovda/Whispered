"""
Whispered UI - YouTube publish dialog.

Shown after a "Video for YouTube" run (or from the YouTube tab) as a
three-step wizard:

1. Texts — the generated title / description (chapter timecodes folded in)
   / tags to correct, plus the speakers' names for the cover.
2. Cover — rendered from those texts through the Cover workspace (palette,
   leaves, photos), with "Regenerate" and a photo for the second speaker
   (with its crop: focal point and zoom);
   "Approve" writes the final ``cover.png``.
3. Publish — validated against YouTube's limits; hand-off actions (copy,
   show the video, save as a folder, open Studio) and, in API mode, the
   upload itself. Nothing is sent over the network from here.

Opened with ``.exec()`` and rebuilt each time, so (per CLAUDE.md's i18n
notes) it is deliberately not retranslated live.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Optional, Protocol

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from application.youtube_publish import (
    DESCRIPTION_MAX_BYTES, TAGS_MAX_CHARS, THUMBNAIL_MAX_BYTES, TITLE_MAX_CHARS, build_package,
    description_bytes, draft_defaults, fit_tags, normalize_titles, parse_tags, record_draft,
    restore_draft, tags_length, validate_package,
)
from application.user_edits import load_overlay, save_overlay
from core.i18n import tr
from core.logger import get_logger
from core.platform_support import reveal_in_file_manager
from domain.youtube_publish import PublishPackage, UploadRecord
from ui.cover_inspector import PhotoFraming
from ui.theme import set_role
from ui.toast import show_toast

logger = get_logger(__name__)

STUDIO_UPLOAD_URL = "https://www.youtube.com/upload"
STUDIO_EDIT_URL = "https://studio.youtube.com/video/{video_id}/edit"


PAGE_TEXTS, PAGE_COVER, PAGE_PUBLISH = range(3)
_COVER_PREVIEW_WIDTH = 640


def studio_edit_url(video_id: str) -> str:
    return STUDIO_EDIT_URL.format(video_id=video_id)


class CoverStudio(Protocol):
    """What the wizard's cover step drives — ``ui.cover_view.CoverView``.
    ``preview_changed`` (a bound pyqtSignal) carries each re-render's QImage."""

    preview_changed: object

    def set_cover_texts(self, title: str, host: str, guest: str) -> None: ...
    def shuffle(self) -> None: ...
    def choose_photo(self, slot: str) -> None: ...
    def grab_frame(self, slot: str) -> None: ...
    def has_video(self) -> bool: ...
    def photo_framing(self, slot: str) -> tuple[tuple[float, float], float]: ...
    def set_photo_framing(
        self, slot: str, focus: tuple[float, float], zoom: float) -> None: ...


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

    *cover_studio* (the Cover workspace) enables the cover step: without it
    the step just shows *cover_path*. *host_name* pre-fills the host.

    *draft_path* (``<artifact_dir>/youtube_publish.draft.json``) keeps the
    wizard's edits across closing and reopening it for the same record:
    written on "Next", on approving the cover and on closing, read back
    here (see ``application.youtube_publish.restore_draft`` for what
    happens when the package changed in between).

    *queue_dir* (the record's artifact dir) and *record_id* enable "Queue
    for upload" on the last step: the approved package is written for
    ``tools/youtube_autoupload.py`` (``application.youtube_autoupload``).
    """

    upload_requested = pyqtSignal(object)
    upload_cancel_requested = pyqtSignal()
    # "Approve cover": the owner renders the final cover.png in the
    # background and answers with set_cover_ready() / set_cover_failed().
    cover_render_requested = pyqtSignal()

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
        cover_studio: Optional[CoverStudio] = None,
        host_name: str = "",
        draft_path: Optional[Path] = None,
        queue_dir: Optional[Path] = None,
        record_id: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("yt_publish_title"))
        self.setMinimumWidth(720)
        self._video_path = video_path
        self._cover_path = cover_path
        self._source_name = source_name or "youtube"
        self._save_dir = save_dir
        self._upload_enabled = upload_enabled
        self._record_path = record_path
        self._pending_path = pending_path
        self._uploading = False
        self._cover_studio = cover_studio
        self._cover_busy = False
        self._language: Optional[str] = texts.get("language")
        self._chapter_check = texts.get("chapter_check")
        self._draft_path = draft_path
        self._queue_dir = queue_dir
        self._record_id = record_id
        self._defaults = draft_defaults(texts, host_name)
        draft = load_overlay(draft_path) if draft_path is not None else {}
        fields, self._outdated_fields = restore_draft(draft, self._defaults)
        self._build_ui(texts, fields)
        self._refresh()
        if cover_studio is not None and fields != self._defaults:
            # Reopened with agreed names/texts: the Cover workspace shows
            # them too, not whatever it held before.
            cover_studio.set_cover_texts(self.cover_text(), self.host_name(), self.guest_name())

    # ------------------------------------------------------------------ UI

    def _build_ui(self, texts: dict, fields: dict[str, str]) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        self._step_label = QLabel()
        set_role(self._step_label, "section-title")
        layout.addWidget(self._step_label)
        self._pages = QStackedWidget()
        self._pages.addWidget(self._texts_page(texts, fields))
        self._pages.addWidget(self._cover_page())
        self._pages.addWidget(self._publish_page())
        layout.addWidget(self._pages, 1)

        self._issues_label = QLabel()
        self._issues_label.setWordWrap(True)
        self._issues_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._issues_label)

        nav = QHBoxLayout()
        self._back_btn = QPushButton(tr("yt_wizard_back"))
        self._back_btn.clicked.connect(self._go_back)
        nav.addWidget(self._back_btn)
        nav.addStretch(1)
        close_btn = QPushButton(tr("yt_publish_close"))
        close_btn.clicked.connect(self.reject)
        nav.addWidget(close_btn)
        self._next_btn = QPushButton()
        self._next_btn.setProperty("variant", "primary")
        self._next_btn.clicked.connect(self._go_next)
        nav.addWidget(self._next_btn)
        layout.addLayout(nav)
        self._show_page(PAGE_TEXTS)

    def _texts_page(self, texts: dict, fields: dict[str, str]) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        intro = QLabel(tr("yt_wizard_intro"))
        intro.setWordWrap(True)
        set_role(intro, "dim")
        layout.addWidget(intro)
        self._draft_note = QLabel(tr("yt_wizard_draft_outdated"))
        self._draft_note.setWordWrap(True)
        set_role(self._draft_note, "warning-text")
        self._draft_note.setVisible(bool(self._outdated_fields))
        layout.addWidget(self._draft_note)

        self._title_combo = QComboBox()
        self._title_combo.setEditable(True)
        self._title_combo.addItems(normalize_titles(texts.get("titles")))
        if fields["title"] != self._title_combo.currentText():
            self._title_combo.setCurrentText(fields["title"])
        self._title_combo.currentTextChanged.connect(self._refresh)
        self._title_counter = QLabel()
        layout.addLayout(self._field_row(
            tr("yt_publish_label_title"), self._title_combo, self._title_counter,
            tr("yt_publish_copy_title"), self._copy_title,
        ))

        self._desc_edit = QPlainTextEdit(fields["description"])
        self._desc_edit.setMinimumHeight(140)
        self._desc_edit.setToolTip(tr("yt_wizard_description_tip"))
        self._desc_edit.textChanged.connect(self._refresh)
        self._desc_counter = QLabel()
        layout.addLayout(self._field_row(
            tr("yt_publish_label_description"), self._desc_edit, self._desc_counter,
            tr("yt_publish_copy_description"), self._copy_description,
        ))

        self._tags_edit = QLineEdit(fields["tags"])
        self._tags_edit.textChanged.connect(self._refresh)
        self._tags_counter = QLabel()
        layout.addLayout(self._field_row(
            tr("yt_publish_label_tags"), self._tags_edit, self._tags_counter,
            tr("yt_publish_copy_tags"), self._copy_tags,
        ))

        speakers = QFormLayout()
        self._host_edit = QLineEdit(fields["host"])
        speakers.addRow(tr("yt_wizard_host"), self._host_edit)
        self._guest_edit = QLineEdit(fields["guest"])
        self._guest_edit.setPlaceholderText(tr("yt_wizard_guest_placeholder"))
        speakers.addRow(tr("yt_wizard_guest"), self._guest_edit)
        self._cover_text_edit = QLineEdit(fields["cover_text"])
        self._cover_text_edit.setPlaceholderText(tr("yt_wizard_cover_text_placeholder"))
        speakers.addRow(tr("yt_wizard_cover_text"), self._cover_text_edit)
        layout.addLayout(speakers)
        return page

    def _cover_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self._cover_preview = QLabel()
        self._cover_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_preview.setMinimumSize(_COVER_PREVIEW_WIDTH, _COVER_PREVIEW_WIDTH * 9 // 16)
        layout.addWidget(self._cover_preview, 1)
        self._cover_status = QLabel()
        self._cover_status.setWordWrap(True)
        set_role(self._cover_status, "dim")
        layout.addWidget(self._cover_status)
        row = QHBoxLayout()
        self._regenerate_btn = QPushButton(tr("yt_wizard_regenerate"))
        self._regenerate_btn.setToolTip(tr("cover_shuffle_tip"))
        self._regenerate_btn.clicked.connect(self._regenerate_cover)
        row.addWidget(self._regenerate_btn)
        self._guest_photo_btn = QPushButton(tr("yt_wizard_guest_photo"))
        self._guest_photo_btn.clicked.connect(self._choose_guest_photo)
        row.addWidget(self._guest_photo_btn)
        self._guest_frame_btn = QPushButton(tr("cover_frame_from_video"))
        self._guest_frame_btn.clicked.connect(self._grab_guest_frame)
        row.addWidget(self._guest_frame_btn)
        row.addStretch(1)
        layout.addLayout(row)
        # The second speaker's crop: a face from a video-call tile is small,
        # so it can be zoomed and pointed at right here.
        self._guest_framing = PhotoFraming()
        layout.addWidget(self._guest_framing)
        if self._cover_studio is not None:
            self._cover_studio.preview_changed.connect(self._show_cover_image)  # type: ignore[attr-defined]
            focus, zoom = self._cover_studio.photo_framing("photo_b")
            self._guest_framing.set_framing(focus, zoom)
            self._guest_framing.framing_changed.connect(self._on_guest_framing)
        else:
            for widget in (self._regenerate_btn, self._guest_photo_btn,
                           self._guest_frame_btn, self._guest_framing):
                widget.setVisible(False)
            self._show_cover_file()
        return page

    def _publish_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._video_row())
        layout.addLayout(self._cover_row())
        summary = QLabel(tr("yt_wizard_publish_hint"))
        summary.setWordWrap(True)
        set_role(summary, "dim")
        layout.addWidget(summary)
        if self._upload_enabled:
            layout.addLayout(self._upload_row())
        if self._queue_dir is not None:
            layout.addLayout(self._queue_row())
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
        layout.addLayout(actions)
        layout.addStretch(1)
        return page

    def _queue_row(self) -> QVBoxLayout:
        box = QVBoxLayout()
        row = QHBoxLayout()
        if not self._upload_enabled:
            # Without API mode there is no upload row to pick visibility in.
            row.addWidget(QLabel(tr("yt_publish_privacy")))
            self._privacy_combo = QComboBox()
            self._privacy_combo.addItem(tr("yt_publish_private"), "private")
            self._privacy_combo.addItem(tr("yt_publish_unlisted"), "unlisted")
            row.addWidget(self._privacy_combo)
        row.addStretch(1)
        self._queue_btn = QPushButton(tr("yt_queue_button"))
        self._queue_btn.setToolTip(tr("yt_queue_tip"))
        if not self._upload_enabled:
            self._queue_btn.setProperty("variant", "primary")
        self._queue_btn.clicked.connect(self._queue_upload)
        row.addWidget(self._queue_btn)
        box.addLayout(row)
        self._queue_status = QLabel()
        self._queue_status.setWordWrap(True)
        self._queue_status.setVisible(False)
        set_role(self._queue_status, "dim")
        box.addWidget(self._queue_status)
        return box

    def _queue_upload(self) -> None:
        """The final approval: hand exactly this package to the upload
        queue (tools/youtube_autoupload.py)."""
        if self._queue_dir is None or self._blocking_issues():
            return
        from application.youtube_autoupload import queue_upload

        try:
            queue_upload(self._queue_dir, self._record_id, self.upload_package())
        except OSError as exc:
            logger.warning("Could not queue the upload in %s: %s", self._queue_dir, exc)
            show_toast(self, tr("yt_queue_failed"), kind="error")
            return
        self.save_draft()
        self._queue_status.setText(tr("yt_queue_done"))
        self._queue_status.setVisible(True)
        show_toast(self, tr("yt_queue_done"), kind="success")

    # ------------------------------------------------------------- wizard

    def current_page(self) -> int:
        return self._pages.currentIndex()

    def _show_page(self, index: int) -> None:
        self._pages.setCurrentIndex(index)
        names = (tr("yt_wizard_step_texts"), tr("yt_wizard_step_cover"),
                 tr("yt_wizard_step_publish"))
        self._step_label.setText(
            tr("yt_wizard_step", n=index + 1, total=len(names), name=names[index]))
        self._back_btn.setVisible(index > PAGE_TEXTS)
        self._next_btn.setVisible(index < PAGE_PUBLISH)
        self._next_btn.setText(
            tr("yt_wizard_to_cover") if index == PAGE_TEXTS else tr("yt_wizard_approve_cover"))
        self._refresh_wizard_buttons()

    def _refresh_wizard_buttons(self) -> None:
        page = self._pages.currentIndex()
        busy = self._cover_busy
        self._next_btn.setEnabled(not busy and not (
            page == PAGE_TEXTS and not self.current_title()))
        self._back_btn.setEnabled(not busy and not self._uploading)
        for widget in (self._regenerate_btn, self._guest_photo_btn, self._guest_framing):
            widget.setEnabled(not busy)
        self._guest_frame_btn.setEnabled(
            not busy and self._cover_studio is not None and self._cover_studio.has_video())

    def _go_back(self) -> None:
        if self._pages.currentIndex() > PAGE_TEXTS:
            self._show_page(self._pages.currentIndex() - 1)

    def _go_next(self) -> None:
        page = self._pages.currentIndex()
        self.save_draft()
        if page == PAGE_TEXTS:
            self._show_page(PAGE_COVER)
            if self._cover_studio is not None:
                self._cover_status.setText("")
                self._cover_studio.set_cover_texts(
                    self.cover_text(), self.host_name(), self.guest_name())
        elif page == PAGE_COVER:
            if self._cover_studio is None:
                self._show_page(PAGE_PUBLISH)
                return
            self._cover_busy = True
            self._cover_status.setText(tr("yt_wizard_cover_saving"))
            self._refresh_wizard_buttons()
            self.cover_render_requested.emit()

    def done(self, result: int) -> None:
        """Closing by any route (Close, Esc, the title bar) keeps the edits."""
        self.save_draft()
        super().done(result)

    def draft_fields(self) -> dict[str, str]:
        """The wizard's fields as they stand — what the draft stores."""
        return {
            "title": self.current_title(),
            "description": self._desc_edit.toPlainText(),
            "tags": self._tags_edit.text().strip(),
            "host": self.host_name(),
            "guest": self.guest_name(),
            "cover_text": self._cover_text_edit.text().strip(),
        }

    def save_draft(self) -> None:
        """Write this record's draft (only what differs from the package as
        the YouTube tab has it; nothing different removes the file)."""
        if self._draft_path is None:
            return
        try:
            save_overlay(self._draft_path, record_draft(self.draft_fields(), self._defaults))
        except (OSError, ValueError) as exc:
            logger.warning("Failed to save the publish wizard draft %s: %s", self._draft_path, exc)

    def cover_text(self) -> str:
        """What the cover says: its own line if typed, else the video title."""
        return self._cover_text_edit.text().strip() or self.current_title()

    def host_name(self) -> str:
        return self._host_edit.text().strip()

    def guest_name(self) -> str:
        return self._guest_edit.text().strip()

    def _on_guest_framing(self, fx: float, fy: float, zoom: float) -> None:
        if self._cover_studio is not None:
            self._cover_studio.set_photo_framing("photo_b", (fx, fy), zoom)

    def _choose_guest_photo(self) -> None:
        if self._cover_studio is not None:
            self._cover_studio.choose_photo("photo_b")

    def _grab_guest_frame(self) -> None:
        if self._cover_studio is not None:
            self._cover_studio.grab_frame("photo_b")

    def _regenerate_cover(self) -> None:
        if self._cover_studio is not None:
            self._cover_studio.shuffle()

    def _show_cover_image(self, image: object) -> None:
        if not isinstance(image, QImage) or image.isNull():
            return
        self._cover_preview.setPixmap(QPixmap.fromImage(image).scaledToWidth(
            _COVER_PREVIEW_WIDTH, Qt.TransformationMode.SmoothTransformation))

    def _show_cover_file(self) -> None:
        pixmap = QPixmap(str(self._cover_path)) if self._cover_path else QPixmap()
        if pixmap.isNull():
            self._cover_preview.setText(tr("yt_publish_no_cover"))
        else:
            self._cover_preview.setPixmap(pixmap.scaledToWidth(
                _COVER_PREVIEW_WIDTH, Qt.TransformationMode.SmoothTransformation))

    def set_cover_ready(self, path: Path) -> None:
        """The approved cover is saved at *path*: use it and move on."""
        self._cover_busy = False
        self._cover_path = path
        self._cover_status.setText("")
        self._update_cover_thumb()
        self._show_page(PAGE_PUBLISH)
        self._refresh()

    def set_cover_failed(self, message: str) -> None:
        self._cover_busy = False
        self._cover_status.setText(tr("yt_wizard_cover_failed", detail=message))
        self._refresh_wizard_buttons()

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
        row.addWidget(self._cover_label, 1)
        self._update_cover_thumb()
        return row

    def _update_cover_thumb(self) -> None:
        pixmap = QPixmap(str(self._cover_path)) if self._cover_path else QPixmap()
        if pixmap.isNull():
            self._cover_label.setText(tr("yt_publish_no_cover"))
            set_role(self._cover_label, "dim")
        else:
            self._cover_label.setPixmap(pixmap.scaledToWidth(
                200, Qt.TransformationMode.SmoothTransformation))

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
        if hasattr(self, "_next_btn"):
            self._refresh_wizard_buttons()

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
            privacy=self._privacy(),
            thumbnail_path=self._thumbnail_for_upload(),
        )

    def _privacy(self) -> str:
        combo = getattr(self, "_privacy_combo", None)
        return str(combo.currentData() or "private") if combo is not None else "private"

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
        self._refresh_wizard_buttons()
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
        self._refresh_wizard_buttons()

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
