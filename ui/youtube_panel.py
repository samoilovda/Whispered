"""
Whispered – YouTube Panel
Generates a YouTube-ready timecode block from a transcript.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QPlainTextEdit, QApplication,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from core.i18n import tr
from core.logger import get_logger
from core.youtube_description import format_youtube_description
from ui.toast import show_toast

logger = get_logger(__name__)


class YouTubePanel(QWidget):
    """YouTube description tab — generates a timecode list from chapters."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments = []
        self._worker = None
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

        self._text_edit = QPlainTextEdit()
        self._text_edit.setReadOnly(True)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._text_edit.setFont(mono)
        self._text_edit.setStyleSheet(
            "QPlainTextEdit { background: #1a1a1a; color: #d0d0d0;"
            " border: 1px solid #333; border-radius: 4px; }"
        )
        layout.addWidget(self._text_edit, stretch=1)

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
        self._text_edit.clear()
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            try:
                self._worker.finished.disconnect(self._on_finished)
                self._worker.error_occurred.disconnect(self._on_error)
            except RuntimeError:
                pass
            self._worker.wait(2000)
        self._worker = None
        self._placeholder.setText(tr("youtube_placeholder"))
        self._placeholder.show()

    # ── Generation ──────────────────────────────────────────────────

    def _generate(self):
        from config import get_config
        lm_url = get_config().lm_studio_url
        if not lm_url:
            self._text_edit.setPlainText(tr("youtube_no_lm"))
            return

        self._gen_btn.setEnabled(False)
        self._gen_btn.setText(tr("youtube_generating"))
        self._copy_btn.setEnabled(False)
        self._text_edit.clear()

        lang = self._lang_combo.currentData()

        from core.insights_worker import InsightsWorker
        self._worker = InsightsWorker(
            "chapters", self._segments, lm_url, language=lang, parent=self
        )
        self._worker.finished.connect(self._on_finished)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, insight_type: str, data):
        if isinstance(data, list):
            text = format_youtube_description(data)
            if text:
                self._text_edit.setPlainText(text)
                self._copy_btn.setEnabled(True)
            else:
                self._text_edit.setPlainText(tr("youtube_empty"))
        else:
            self._text_edit.setPlainText(f"{tr('youtube_error')} {data}")
        self._reset_button()

    def _on_error(self, insight_type: str, msg: str):
        self._text_edit.setPlainText(f"{tr('youtube_error')} {msg}")
        self._reset_button()

    def _reset_button(self):
        self._gen_btn.setEnabled(bool(self._segments))
        self._gen_btn.setText(tr("youtube_generate"))

    def _copy_to_clipboard(self):
        text = self._text_edit.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            show_toast(self, tr("youtube_copied"), kind="success")
