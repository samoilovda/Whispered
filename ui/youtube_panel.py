"""
Whispered – YouTube Panel
Generates YouTube-ready titles, description, tags, and chapter timecodes.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QPlainTextEdit, QApplication, QTabWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from core.i18n import tr
from core.logger import get_logger
from core.youtube_description import format_youtube_description
from ui.toast import show_toast

logger = get_logger(__name__)

_YT_TYPES = ("chapters", "yt_titles", "yt_description", "yt_tags", "yt_questions")


class YouTubePanel(QWidget):
    """YouTube tab — generates titles, description, tags, and timecode chapters."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments = []
        self._workers: dict = {}   # insight_type → worker
        self._pending = 0
        self._setup_ui()

    # ── UI ──────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._placeholder = QLabel(tr("youtube_placeholder"))
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet("color: #555; font-size: 13px;")
        layout.addWidget(self._placeholder)

        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._lang_combo = QComboBox()
        self._lang_combo.addItem(tr("youtube_lang_auto"), None)
        self._lang_combo.addItem("Русский", "Russian")
        self._lang_combo.addItem("English", "English")
        controls.addWidget(self._lang_combo)

        self._provider_combo = QComboBox()
        self._provider_combo.addItem(tr("provider_lmstudio"), "lmstudio")
        self._provider_combo.addItem(tr("provider_openai"), "openai")
        self._provider_combo.addItem(tr("provider_anthropic"), "anthropic")
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        controls.addWidget(self._provider_combo)

        self._configure_btn = QPushButton(tr("provider_configure"))
        self._configure_btn.setEnabled(False)
        self._configure_btn.clicked.connect(self._open_provider_dialog)
        controls.addWidget(self._configure_btn)

        self._gen_btn = QPushButton(tr("youtube_generate"))
        self._gen_btn.setProperty("variant", "primary")
        self._gen_btn.setEnabled(False)
        self._gen_btn.clicked.connect(self._generate)
        controls.addWidget(self._gen_btn)

        controls.addStretch()

        self._copy_btn = QPushButton(tr("youtube_copy"))
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._copy_to_clipboard)
        controls.addWidget(self._copy_btn)

        layout.addLayout(controls)

        self._privacy_notice = QLabel(tr("youtube_privacy_notice"))
        self._privacy_notice.setWordWrap(True)
        self._privacy_notice.setStyleSheet("color: #d0a030; font-size: 11px;")
        self._privacy_notice.setVisible(False)
        layout.addWidget(self._privacy_notice)

        self._init_provider_from_config()

        # Inner tabs: Chapters | Titles | Description | Tags
        self._tabs = QTabWidget()
        self._tabs.setVisible(False)

        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        _style = (
            "QPlainTextEdit { background: #1a1a1a; color: #d0d0d0;"
            " border: 1px solid #333; border-radius: 4px; }"
        )

        self._chapters_edit = QPlainTextEdit()
        self._chapters_edit.setReadOnly(True)
        self._chapters_edit.setFont(mono)
        self._chapters_edit.setStyleSheet(_style)
        self._tabs.addTab(self._chapters_edit, tr("yt_tab_chapters"))

        self._titles_edit = QPlainTextEdit()
        self._titles_edit.setReadOnly(True)
        self._titles_edit.setFont(mono)
        self._titles_edit.setStyleSheet(_style)
        self._tabs.addTab(self._titles_edit, tr("yt_tab_titles"))

        self._desc_edit = QPlainTextEdit()
        self._desc_edit.setReadOnly(True)
        self._desc_edit.setStyleSheet(_style)
        self._tabs.addTab(self._desc_edit, tr("yt_tab_description"))

        self._tags_edit = QPlainTextEdit()
        self._tags_edit.setReadOnly(True)
        self._tags_edit.setFont(mono)
        self._tags_edit.setStyleSheet(_style)
        self._tabs.addTab(self._tags_edit, tr("yt_tab_tags"))

        self._questions_edit = QPlainTextEdit()
        self._questions_edit.setReadOnly(True)
        self._questions_edit.setFont(mono)
        self._questions_edit.setStyleSheet(_style)
        self._tabs.addTab(self._questions_edit, tr("yt_tab_questions"))

        layout.addWidget(self._tabs, stretch=1)

    # ── Provider selection ─────────────────────────────────────────

    def _init_provider_from_config(self):
        from config import get_config
        kind = get_config().yt_provider
        idx = self._provider_combo.findData(kind)
        self._provider_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._on_provider_changed()

    def _on_provider_changed(self):
        from config import get_config, save_config
        kind = self._provider_combo.currentData()
        cfg = get_config()
        if cfg.yt_provider != kind:
            cfg.yt_provider = kind
            save_config()
        is_cloud = kind != "lmstudio"
        self._configure_btn.setEnabled(is_cloud)
        self._privacy_notice.setVisible(is_cloud)

    def _open_provider_dialog(self):
        from ui.provider_dialog import ProviderDialog
        kind = self._provider_combo.currentData()
        dialog = ProviderDialog(kind=kind, parent=self)
        dialog.exec()

    # ── Public API ──────────────────────────────────────────────────

    def set_segments(self, segments) -> None:
        self._segments = segments
        self._gen_btn.setEnabled(bool(segments))
        if segments:
            self._placeholder.hide()
        else:
            self._placeholder.show()

    def clear(self) -> None:
        self._segments = []
        self._gen_btn.setEnabled(False)
        self._copy_btn.setEnabled(False)
        for edit in (self._chapters_edit, self._titles_edit,
                     self._desc_edit, self._tags_edit, self._questions_edit):
            edit.clear()
        self._tabs.setVisible(False)
        for w in self._workers.values():
            if w and w.isRunning():
                w.cancel()
                w.wait(2000)
        self._workers.clear()
        self._pending = 0
        self._placeholder.setText(tr("youtube_placeholder"))
        self._placeholder.show()

    # ── Generation ──────────────────────────────────────────────────

    def _generate(self):
        from config import get_config
        from core.ai_provider import provider_from_config
        from core.insights_worker import InsightsWorker

        cfg = get_config()
        provider = provider_from_config(cfg)

        if provider.kind != "lmstudio" and not provider.api_key:
            self._chapters_edit.setPlainText(tr("youtube_no_api_key"))
            self._tabs.setVisible(True)
            self._placeholder.hide()
            return

        if provider.kind == "lmstudio" and not cfg.lm_studio_url:
            self._chapters_edit.setPlainText(tr("youtube_no_lm"))
            self._tabs.setVisible(True)
            self._placeholder.hide()
            return

        # Cancel any in-progress workers
        for w in self._workers.values():
            if w and w.isRunning():
                w.cancel()
                w.wait(1000)
        self._workers.clear()

        self._gen_btn.setEnabled(False)
        self._gen_btn.setText(tr("youtube_generating"))
        self._copy_btn.setEnabled(False)
        for edit in (self._chapters_edit, self._titles_edit,
                     self._desc_edit, self._tags_edit, self._questions_edit):
            edit.clear()
        self._tabs.setVisible(True)
        self._placeholder.hide()

        lang = self._lang_combo.currentData()
        self._pending = len(_YT_TYPES)

        for yt_type in _YT_TYPES:
            worker = InsightsWorker(
                yt_type, self._segments, cfg.lm_studio_url, language=lang,
                provider=(None if provider.kind == "lmstudio" else provider),
                parent=self,
            )
            worker.finished.connect(self._on_finished)
            worker.error_occurred.connect(self._on_error)
            self._workers[yt_type] = worker
            worker.start()

    def _on_finished(self, insight_type: str, data):
        self._pending = max(0, self._pending - 1)

        if insight_type == "chapters":
            if isinstance(data, list):
                text = format_youtube_description(data)
                self._chapters_edit.setPlainText(text or tr("youtube_empty"))
            else:
                self._chapters_edit.setPlainText(str(data) if data else tr("youtube_empty"))

        elif insight_type == "yt_titles":
            if isinstance(data, list):
                self._titles_edit.setPlainText("\n\n".join(f"{i+1}. {t}" for i, t in enumerate(data)))
            else:
                self._titles_edit.setPlainText(str(data) if data else "")

        elif insight_type == "yt_description":
            if isinstance(data, list) and data:
                self._desc_edit.setPlainText(data[0] if isinstance(data[0], str) else str(data[0]))
            elif isinstance(data, str):
                self._desc_edit.setPlainText(data)

        elif insight_type == "yt_tags":
            if isinstance(data, list):
                self._tags_edit.setPlainText(", ".join(data))
            elif isinstance(data, str):
                self._tags_edit.setPlainText(data)

        elif insight_type == "yt_questions":
            if isinstance(data, list):
                text = format_youtube_description(data)
                self._questions_edit.setPlainText(text or tr("youtube_empty"))
            else:
                self._questions_edit.setPlainText(str(data) if data else tr("youtube_empty"))

        if self._pending == 0:
            self._reset_button()
            self._copy_btn.setEnabled(True)

    def _on_error(self, insight_type: str, msg: str):
        logger.warning("YouTube worker error (%s): %s", insight_type, msg)
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self._reset_button()

    def _reset_button(self):
        self._gen_btn.setEnabled(bool(self._segments))
        self._gen_btn.setText(tr("youtube_generate"))

    def _copy_to_clipboard(self):
        """Copy the content of the currently-visible inner tab."""
        edit_map = {
            0: self._chapters_edit,
            1: self._titles_edit,
            2: self._desc_edit,
            3: self._tags_edit,
            4: self._questions_edit,
        }
        edit = edit_map.get(self._tabs.currentIndex(), self._chapters_edit)
        text = edit.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            show_toast(self, tr("youtube_copied"), kind="success")
