"""
Whispered – Chat Panel
Ask questions about the transcript using a local LM Studio model.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel,
    QLineEdit, QPushButton, QFrame, QSizePolicy, QMessageBox, QFileDialog,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont

from core.llm_text import link_citations

from core.logger import get_logger
from core.i18n import tr
from ui.i18n_helpers import Retranslator
from core.worker_registry import WorkerRegistry
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
    """Single chat message bubble. An assistant's "[12:34]" citations are
    links (seek:<seconds>) — see ChatPanel.seek_requested."""

    def __init__(self, text: str, role: str, parent=None):
        super().__init__(parent)
        self._role = role
        self._raw = ""
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.MarkdownText)
        self.setOpenExternalLinks(False)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.setText(text)
        self.setFont(QFont("Sans", 10))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setProperty("role", "chat-bubble-user" if role == "user" else "chat-bubble-assistant")

    def setText(self, text: str | None) -> None:  # noqa: N802
        self._raw = text or ""
        shown = link_citations(self._raw) if self._role != "user" else self._raw
        super().setText(shown)

    def text(self) -> str:  # noqa: D102 - the message as written, not as linked
        return self._raw


class _ChatRow(QWidget):
    """Horizontal wrapper row that aligns a bubble to the left or right."""

    def __init__(self, text: str, role: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(0)

        self.bubble = _Bubble(text, role, self)
        self.bubble.setMaximumWidth(600)

        if role == "user":
            layout.addStretch()
            layout.addWidget(self.bubble)
        else:
            layout.addWidget(self.bubble)
            layout.addStretch()




class ChatPanel(QWidget):
    """Chat tab — 'Ask your recording.'"""

    # A citation in an answer was clicked: seconds into the recording.
    seek_requested = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._transcript: str = ""
        self._context_lines: list[str] | None = None
        # [{role, content, status, revision, id}] — the conversation shown,
        # in order; status is complete / cancelled / failed (UX-07).
        self._history: list[dict] = []
        # Whose conversation this is: the record id (None when history is
        # off or nothing is saved yet) and the transcript revision the
        # context was built from. Identity is the record, never the text —
        # two records with the same words are two conversations.
        self._record_id: int | None = None
        self._revision = ""
        self._persist = False
        # Bumped on every context switch; a worker's late token or answer
        # carries the generation it was started in and is dropped if stale.
        self._generation = 0
        self._worker = None
        self._registry = WorkerRegistry(parent=self)
        self._current_bubble: Optional[_Bubble] = None
        self._token_buf: list[str] = []
        self._scroll_timer: Optional[QTimer] = None
        self._quick_chip_buttons: list = []
        self._i18n = Retranslator()
        self._setup_ui()
        self._i18n.call(self._retranslate_chat)
        self._i18n.bind()

    def _retranslate_chat(self) -> None:
        self._input.setPlaceholderText(tr("chat_input_placeholder"))
        self._send_btn.setText(tr("chat_send"))
        self._stop_btn.setText(tr("chat_stop"))
        self._clear_btn.setText(tr("btn_clear"))
        self._export_btn.setText(tr("chat_export"))
        self._not_saved_label.setText(tr("chat_not_saved"))
        self._placeholder.setText(
            tr("chat_ready") if self._transcript else tr("chat_placeholder")
        )
        questions = _quick_questions()
        for btn, text in zip(self._quick_chip_buttons, questions):
            btn.setText(text)

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
        for i, q in enumerate(_quick_questions()):
            btn = QPushButton(q)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setProperty("role", "quick-chip")
            # Re-resolve the question text at click time so a chip clicked
            # after a language switch sends the current-language prompt.
            btn.clicked.connect(
                lambda _, idx=i: self._send(_quick_questions()[idx])
            )
            chips_row.addWidget(btn)
            self._quick_chip_buttons.append(btn)
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

        self._clear_btn = QPushButton(tr("btn_clear"))
        self._clear_btn.clicked.connect(self._clear_chat)
        input_row.addWidget(self._clear_btn)

        self._export_btn = QPushButton(tr("chat_export"))
        self._export_btn.clicked.connect(self._export_chat)
        input_row.addWidget(self._export_btn)

        layout.addLayout(input_row)

        # History off: say plainly that this conversation is not kept,
        # rather than keeping it behind the user's back (UX-07).
        self._not_saved_label = QLabel(tr("chat_not_saved"))
        self._not_saved_label.setProperty("role", "muted")
        self._not_saved_label.setWordWrap(True)
        self._not_saved_label.setVisible(False)
        layout.addWidget(self._not_saved_label)

    # ------------------------------------------------------------------ public

    def set_record_context(
        self, record_id: int | None, revision: str, text: str, segments=None,
        *, persist: bool = True,
    ) -> None:
        """Point the chat at *record_id*'s transcript (UX-07).

        A different record (or a new unsaved result) switches to that
        record's own stored conversation; the in-flight answer of the
        previous one is cancelled and can no longer land here. The same
        record with a new *revision* keeps the conversation and marks the
        change, so earlier answers read as being about the earlier text."""
        if segments is not None:
            from core.llm_text import timestamped_blocks

            self._context_lines = timestamped_blocks(segments)
        same_record = record_id is not None and record_id == self._record_id
        self._transcript = text
        if same_record:
            if revision != self._revision and self._history:
                self._add_divider()
            self._revision = revision
            return
        self._switch_conversation(record_id, revision, persist=persist)

    def set_transcript(self, text: str, segments=None) -> None:
        """A transcript with no record behind it (kept for callers that
        have none): an unsaved conversation of its own."""
        self.set_record_context(None, "", text, segments, persist=False)

    def clear_transcript(self) -> None:
        self._switch_conversation(None, "", persist=False)
        self._transcript = ""
        self._context_lines = None
        self._placeholder.setText(tr("chat_placeholder"))

    def _switch_conversation(self, record_id: int | None, revision: str, *, persist: bool) -> None:
        self._generation += 1
        self._cancel_worker()
        if self._scroll_timer:
            self._scroll_timer.stop()
        self._current_bubble = None
        self._token_buf = []
        self._remove_bubbles()
        self._history = []
        self._record_id = record_id
        self._revision = revision
        self._persist = persist and record_id is not None
        self._not_saved_label.setVisible(not self._persist and bool(self._transcript))
        if self._persist:
            self._load_conversation()
        self._reset_input()
        self._show_placeholder_if_empty()

    def _store(self):
        from core.history import get_history_store

        return get_history_store()

    def _load_conversation(self) -> None:
        try:
            messages = self._store().list_chat_messages(self._record_id)
        except Exception as exc:
            logger.warning("Chat for record %s unavailable: %s", self._record_id, exc)
            return
        last_revision = None
        for message in messages:
            if last_revision is not None and message["revision"] != last_revision:
                self._add_divider()
            last_revision = message["revision"]
            self._history.append(message)
            bubble = self._add_bubble(_shown_text(message), message["role"])
            if message["status"] == "failed":
                set_role(bubble, "chat-bubble-error")
        if messages and last_revision != self._revision:
            self._add_divider()

    def _save_message(self, role: str, content: str, status: str) -> dict:
        message = {"role": role, "content": content, "status": status,
                   "revision": self._revision, "id": None}
        if self._persist:
            try:
                from config import get_config

                message["id"] = self._store().add_chat_message(
                    self._record_id, role, content, revision=self._revision,
                    status=status, model=getattr(get_config(), "lm_studio_model", "") or "",
                )
            except Exception as exc:
                logger.warning("Failed to keep a chat message: %s", exc)
        self._history.append(message)
        return message

    def _set_status(self, message: dict, status: str) -> None:
        message["status"] = status
        if self._persist and message.get("id") is not None:
            try:
                self._store().set_chat_message_status(message["id"], status)
            except Exception as exc:
                logger.warning("Failed to update a chat message: %s", exc)

    def _model_history(self) -> list[dict]:
        """What the model sees of the conversation: only complete
        question/answer pairs — a cancelled or failed turn isn't context."""
        turns: list[dict] = []
        pending: dict | None = None
        for message in self._history:
            if message["role"] == "user":
                pending = message if message["status"] == "complete" else None
            elif message["role"] == "assistant" and pending is not None:
                if message["status"] == "complete":
                    turns.append({"role": "user", "content": pending["content"]})
                    turns.append({"role": "assistant", "content": message["content"]})
                pending = None
        return turns

    def _add_divider(self) -> None:
        label = QLabel(tr("chat_revision_changed"))
        label.setProperty("role", "list-group-header")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count = self._msg_layout.count()
        self._msg_layout.insertWidget(count - 1, label)

    def _remove_bubbles(self) -> None:
        # Keep the placeholder (index 0) and the trailing stretch.
        while self._msg_layout.count() > 2:
            item = self._msg_layout.takeAt(1)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _show_placeholder_if_empty(self) -> None:
        self._placeholder.setText(tr("chat_ready") if self._transcript else tr("chat_placeholder"))
        self._placeholder.setVisible(not self._history)

    def _export_chat(self) -> None:
        """Save the conversation as Markdown, statuses included."""
        if not self._history:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("chat_export"), "chat.md", "Markdown (*.md);;All Files (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(conversation_markdown(self._history))
        except OSError as exc:
            QMessageBox.warning(self, tr("chat_export"), str(exc))

    def shutdown(self) -> None:
        """Stop any in-flight chat worker. Call from MainWindow.closeEvent.

        Unlike _cancel_worker() (used by interactive Stop/Clear, which must
        not block the GUI thread), this bounds the wait: if the whole
        process exits shortly after closeEvent returns, a worker given no
        wall-clock time at all to stop is what Qt aborts the process for.
        A worker that outlives the bound is still not abandoned — it stays
        alive in the registry until it actually finishes.
        """
        if self._scroll_timer:
            self._scroll_timer.stop()
        self._registry.shutdown_all(timeout_ms=5000)

    def _cancel_worker(self) -> None:
        """Cancel the in-flight chat worker without blocking the GUI thread.

        The previous ``cancel()`` + ``wait(5000)`` froze the UI for up to
        5 s on a Stop/Clear button click, and abandoned the worker outright
        if the LM Studio stream didn't unblock in time. ``retire()``
        disconnects its signals immediately (a late token/turn_finished
        can't land on a bubble the user already considers stopped) and
        hands it to WorkerRegistry, which deletes it once its QThread
        actually finishes.
        """
        if self._worker is not None and self._worker.isRunning():
            self._registry.retire(self._worker)

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
        sys_prompt = _build_system_prompt(
            self._transcript, max_chars=context_chars, lines=self._context_lines,
        )

        # Build message list: system + complete turns + new user turn
        messages: list[dict] = [{"role": "system", "content": sys_prompt}]
        messages.extend(self._model_history())
        messages.append({"role": "user", "content": text})

        # Kept at once, so a stop or crash still leaves the question.
        self._pending_user = self._save_message("user", text, "complete")

        from core.chat_worker import ChatWorker
        self._worker = ChatWorker(
            messages=messages,
            lm_url=cfg.lm_studio_url,
            parent=self,
        )
        self._token_buf = []
        generation = self._generation
        self._worker.token_received.connect(
            lambda delta, g=generation: g == self._generation and self._on_token(delta))
        self._worker.turn_finished.connect(
            lambda full, g=generation: g == self._generation and self._on_finished(full))
        self._worker.error_occurred.connect(
            lambda msg, g=generation: g == self._generation and self._on_error(msg))
        self._registry.register(self._worker, name="chat")
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
        self._save_message("assistant", full_text, "complete")
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
        # The question stays visible, marked failed: not context for the
        # model, not silently erased either.
        pending = getattr(self, "_pending_user", None)
        if pending is not None:
            self._set_status(pending, "failed")
        self._current_bubble = None
        self._reset_input()

    def _on_stop(self):
        if self._scroll_timer:
            self._scroll_timer.stop()
        self._cancel_worker()
        if self._current_bubble:
            text = self._current_bubble.text()
            if text and text != "…":
                # A partial answer is kept, labelled as incomplete.
                self._save_message("assistant", text, "cancelled")
                self._current_bubble.setText(_shown_text(
                    {"content": text, "status": "cancelled", "role": "assistant"}))
            else:
                self._current_bubble.setText(tr("chat_cancelled"))
                pending = getattr(self, "_pending_user", None)
                if pending is not None:
                    self._set_status(pending, "cancelled")
        self._current_bubble = None
        self._reset_input()

    def _reset_input(self):
        self._send_btn.setEnabled(True)
        self._stop_btn.setVisible(False)
        self._input.setEnabled(True)
        self._input.setFocus()

    def _add_bubble(self, text: str, role: str) -> _Bubble:
        row = _ChatRow(text, role, self._msg_container)
        row.bubble.linkActivated.connect(self._on_link)
        # Insert before the trailing stretch (last item)
        count = self._msg_layout.count()
        self._msg_layout.insertWidget(count - 1, row)
        QTimer.singleShot(0, self._scroll_to_bottom)
        return row.bubble

    def _on_link(self, href: str) -> None:
        if href.startswith("seek:"):
            try:
                self.seek_requested.emit(float(href[5:]))
            except ValueError:
                pass

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
        self._generation += 1
        self._cancel_worker()
        self._history.clear()
        # "Clear" is the one explicit way a kept conversation goes away.
        if self._persist:
            try:
                self._store().clear_chat(self._record_id)
            except Exception as exc:
                logger.warning("Failed to clear the chat: %s", exc)
        self._remove_bubbles()
        if self._transcript:
            self._placeholder.setText(tr("chat_ready"))
            self._placeholder.show()
        else:
            self._placeholder.setText(tr("chat_placeholder"))
            self._placeholder.show()
        self._current_bubble = None


def _shown_text(message: dict) -> str:
    """A stored message as its bubble shows it — incomplete answers say so."""
    if message.get("role") == "assistant" and message.get("status") == "cancelled":
        return message["content"] + "\n\n_" + tr("chat_incomplete") + "_"
    if message.get("role") == "user" and message.get("status") == "failed":
        return message["content"] + "\n\n_" + tr("chat_failed_turn") + "_"
    return message["content"]


def conversation_markdown(history: list[dict]) -> str:
    """The conversation as Markdown, for export."""
    lines = []
    revision = None
    for message in history:
        if revision is not None and message.get("revision") != revision:
            lines.append(f"---\n_{tr('chat_revision_changed')}_\n")
        revision = message.get("revision")
        who = tr("chat_you") if message["role"] == "user" else tr("chat_assistant")
        lines.append(f"**{who}:** {_shown_text(message)}\n")
    return "\n".join(lines)
