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

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from application.youtube_publish import (
    DESCRIPTION_MAX_BYTES, TAGS_MAX_CHARS, TITLE_MAX_CHARS, build_package,
    description_bytes, fit_tags, normalize_titles, parse_tags, tags_length,
    validate_package,
)
from core.i18n import tr
from core.logger import get_logger
from core.platform_support import reveal_in_file_manager
from domain.youtube_publish import PublishPackage
from ui.theme import set_role
from ui.toast import show_toast

logger = get_logger(__name__)

STUDIO_UPLOAD_URL = "https://www.youtube.com/upload"


class YouTubePublishDialog(QDialog):
    """Hand-off dialog for one finished YouTube package.

    *texts* is ``YouTubePanel.publish_texts()``; *video_path* the source
    video when known (``None`` leaves the user to choose one); *cover_path*
    the rendered ``cover.png``; *save_dir* where "Save package" writes its
    ``<source_name>_youtube`` folder.
    """

    def __init__(
        self,
        texts: dict,
        *,
        video_path: Optional[Path] = None,
        cover_path: Optional[Path] = None,
        source_name: str = "",
        save_dir: Optional[Path] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("yt_publish_title"))
        self.setMinimumWidth(560)
        self._video_path = video_path
        self._cover_path = cover_path
        self._source_name = source_name or "youtube"
        self._save_dir = save_dir
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

        actions = QHBoxLayout()
        self._reveal_btn = QPushButton(tr("yt_publish_reveal"))
        self._reveal_btn.clicked.connect(self._reveal_video)
        actions.addWidget(self._reveal_btn)
        self._save_btn = QPushButton(tr("yt_publish_save_package"))
        self._save_btn.clicked.connect(self._save_package)
        actions.addWidget(self._save_btn)
        actions.addStretch(1)
        self._studio_btn = QPushButton(tr("yt_publish_open_studio"))
        self._studio_btn.setProperty("variant", "primary")
        self._studio_btn.clicked.connect(self._open_studio)
        actions.addWidget(self._studio_btn)
        close_btn = QPushButton(tr("yt_publish_close"))
        close_btn.clicked.connect(self.reject)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

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
