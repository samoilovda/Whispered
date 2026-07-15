"""
Whispered – Chat Panel
Ask questions about the transcript using a local LM Studio model.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel,
    QLineEdit, QPushButton, QFrame, QSizePolicy, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

from core.logger import get_logger
from core.i18n import tr
from ui.theme import set_role

logger = get_logger(__name__)

# Quick chips shown below the input bar
_QUICK_QUESTIONS_EN = [
    "What is this recording about?",
    "What decisions were made?",
    "List action items",
    "Summarize in 3 sentences",
]
_QUICK_QUESTIONS_RU = [
    "О чём эта запись?",
    "Какие решения приняты?",
    "Составь список задач",
    "Краткое резюме",
]


def _quick_questions() -> list[str]:
    from core.i18n import current_lang
    return _QUICK_QUESTIONS_RU if current_lang() == "ru" else _QUICK_QUESTIONS_EN


class _Bubble(QLabel):
    """Single chat message bubble."""

    def __init__(self, text: str, role: str, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.MarkdownText)
        self.setText(text)
        self.setFont(QFont("Sans", 10))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setProperty("role", "chat-bubble-user" if role == "user" else "chat-bubble-assistant")




class ChatPanel(QWidget):
    """Chat tab — 'Ask your recording.'"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._transcript: str = ""
        self._history: list[dict] = []   # [{role, content}, …]
        self._worker = None
        self._current_bubble: Optional[_Bubble] = None
        self._token_buf: list[str] = []
        self._scroll_timer: Optional[QTimer] = None
        self._setup_ui()

    # ------------------------------------------------------------------ UI

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # ── Message area ─────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._msg_container = QWidget()
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(4, 4, 4, 4)
        self._msg_layout.setSpacing(8)
        self._msg_layout.addStretch()

        self._scroll.setWidget(self._msg_container)
        layout.addWidget(self._scroll, stretch=1)

        # ── Empty-state placeholder ───────────────────────────────────
        self._placeholder = QLabel(tr("chat_placeholder"))
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setProperty("role", "dim")
        self._placeholder.setStyleSheet("font-size: 13px;")
        self._placeholder.setWordWrap(True)
        self._msg_layout.insertWidget(0, self._placeholder)

        # ── Quick chips ───────────────────────────────────────────────
        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)
        for q in _quick_questions():
            btn = QPushButton(q)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setProperty("role", "quick-chip")
            btn.clicked.connect(lambda _, text=q: self._send(text))
            chips_row.addWidget(btn)
        layout.addLayout(chips_row)

        # ── Input row ─────────────────────────────────────────────────
        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self._input = QLineEdit()
        self._input.setPlaceholderText(tr("chat_input_placeholder"))
        self._input.returnPressed.connect(self._on_send)
        input_row.addWidget(self._input, stretch=1)

        self._send_btn = QPushButton(tr("chat_send"))
        self._send_btn.setProperty("variant", "primary")
        self._send_btn.clicked.connect(self._on_send)
        input_row.addWidget(self._send_btn)

        self._stop_btn = QPushButton(tr("chat_stop"))
        self._stop_btn.setProperty("variant", "danger")
        self._stop_btn.setVisible(False)
        self._stop_btn.clicked.connect(self._on_stop)
        input_row.addWidget(self._stop_btn)

        clear_btn = QPushButton(tr("btn_clear"))
        clear_btn.clicked.connect(self._clear_chat)
        input_row.addWidget(clear_btn)

        layout.addLayout(input_row)

    # ------------------------------------------------------------------ public

    def set_transcript(self, text: str) -> None:
        """Update the transcript context. Optionally ask to reset history."""
        if text == self._transcript:
            return
        if self._history:
            # Silently reset — new transcription overwrites context
            self._clear_chat(confirm=False)
        self._transcript = text
        self._placeholder.setText(tr("chat_ready"))

    def clear_transcript(self) -> None:
        self._transcript = ""
        self._clear_chat(confirm=False)
        self._placeholder.setText(tr("chat_placeholder"))

    def shutdown(self) -> None:
        """Stop any in-flight chat worker. Call from MainWindow.closeEvent."""
        if self._scroll_timer:
            self._scroll_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()

    # ------------------------------------------------------------------ internals

    def _on_send(self):
        text = self._input.text().strip()
        if text:
            self._input.clear()
            self._send(text)

    def _send(self, text: str):
        if not self._transcript:
            QMessageBox.information(self, tr("app_title"), tr("chat_no_transcript"))
            return
        if self._worker and self._worker.isRunning():
            return

        self._placeholder.hide()
        self._add_bubble(text, "user")
        self._current_bubble = self._add_bubble("…", "assistant")

        from config import get_config
        cfg = get_config()

        from core.chat_worker import _build_system_prompt, _CONTEXT_CHARS
        context_chars = getattr(cfg, "chat_context_chars", _CONTEXT_CHARS)
        sys_prompt = _build_system_prompt(self._transcript, max_chars=context_chars)

        # Build message list: system + history + new user turn
        messages: list[dict] = [{"role": "system", "content": sys_prompt}]
        messages.extend(self._history)
        messages.append({"role": "user", "content": text})

        # Optimistically add to history so it's preserved on stop
        self._history.append({"role": "user", "content": text})

        from core.chat_worker import ChatWorker
        self._worker = ChatWorker(
            messages=messages,
            lm_url=cfg.lm_studio_url,
            parent=self,
        )
        self._token_buf = []
        self._worker.token_received.connect(self._on_token)
        self._worker.turn_finished.connect(self._on_finished)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

        # Coalesce scroll-to-bottom at ~10 Hz so we don't scroll on every token
        if self._scroll_timer is None:
            self._scroll_timer = QTimer(self)
            self._scroll_timer.timeout.connect(self._scroll_to_bottom)
        self._scroll_timer.start(100)

        self._send_btn.setEnabled(False)
        self._stop_btn.setVisible(True)
        self._input.setEnabled(False)

    def _on_token(self, delta: str):
        if self._current_bubble:
            if self._token_buf or delta:  # replace placeholder on first real token
                self._token_buf.append(delta)
                self._current_bubble.setText("".join(self._token_buf))

    def _on_finished(self, full_text: str):
        if self._scroll_timer:
            self._scroll_timer.stop()
        self._token_buf = []
        if self._current_bubble:
            self._current_bubble.setText(full_text)
        self._history.append({"role": "assistant", "content": full_text})
        self._current_bubble = None
        self._scroll_to_bottom()
        self._reset_input()

    def _on_error(self, msg: str):
        if self._scroll_timer:
            self._scroll_timer.stop()
        self._token_buf = []
        if self._current_bubble:
            self._current_bubble.setText(f"⚠ {msg}")
            set_role(self._current_bubble, "chat-bubble-error")
        if self._history and self._history[-1]["role"] == "user":
            self._history.pop()   # remove the failed user turn
        self._current_bubble = None
        self._reset_input()

    def _on_stop(self):
        if self._scroll_timer:
            self._scroll_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()
        if self._current_bubble:
            text = self._current_bubble.text()
            if text and text != "…":
                self._history.append({"role": "assistant", "content": text})
        self._current_bubble = None
        if self._history and self._history[-1]["role"] == "user":
            self._history.pop()   # incomplete turn
        self._reset_input()

    def _reset_input(self):
        self._send_btn.setEnabled(True)
        self._stop_btn.setVisible(False)
        self._input.setEnabled(True)
        self._input.setFocus()

    def _add_bubble(self, text: str, role: str) -> _Bubble:
        bubble = _Bubble(text, role, self._msg_container)
        # Insert before the trailing stretch (last item)
        count = self._msg_layout.count()
        self._msg_layout.insertWidget(count - 1, bubble)
        QTimer.singleShot(0, self._scroll_to_bottom)
        return bubble

    def _scroll_to_bottom(self):
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _clear_chat(self, confirm: bool = True):
        if confirm and self._history:
            reply = QMessageBox.question(
                self, tr("btn_clear"), tr("chat_clear_confirm"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait()
        self._history.clear()
        # Remove all bubbles (keep stretch and placeholder)
        while self._msg_layout.count() > 2:
            item = self._msg_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        if self._transcript:
            self._placeholder.setText(tr("chat_ready"))
            self._placeholder.show()
        else:
            self._placeholder.setText(tr("chat_placeholder"))
            self._placeholder.show()
        self._current_bubble = None
