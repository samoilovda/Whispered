"""
Whispered – AI Chat Worker
QThread that sends a user message to LM Studio with the transcript
as system context and streams tokens back to the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3

from PyQt6.QtCore import pyqtSignal

from core.base_worker import BaseWorker
from core.i18n import tr
from core.logger import get_logger

logger = get_logger(__name__)

_CONTEXT_CHARS = 48_000   # default: ~ 12 k tokens

@dataclass(frozen=True)
class ChatContext:
    """The system prompt plus what of the record it actually contains
    (UI/UX audit 2026-10-18, UX-13): the chat shows this before a question
    is sent, and checks an answer's citations against it."""

    prompt: str
    sampled: bool
    total_blocks: int
    kept_blocks: tuple[int, ...]   # indices into the timestamped blocks
    max_chars: int
    retrieved: bool = False


def chat_context(
    transcript: str, max_chars: int = _CONTEXT_CHARS, lines: list[str] | None = None,
    *, question: str = "",
) -> ChatContext:
    """Build the chat's context. With *lines* (core.llm_text.
    timestamped_blocks) the model sees "[MM:SS]" blocks sampled evenly
    across the whole recording and is asked to cite times; with plain
    *transcript* only, the head that fits (the old behaviour)."""
    if lines:
        from core.llm_text import sample_lines_evenly, sampled_line_indices

        sampled = sum(len(line) + 1 for line in lines) - 1 > max_chars
        ranked = _matching_blocks(lines, question, max_chars) if question and sampled else ()
        body = "\n".join(lines[index] for index in ranked) if ranked else sample_lines_evenly(lines, max_chars)
        kept = ranked or tuple(sampled_line_indices(lines, max_chars))
        suffix = "\n\n" + tr("chat_transcript_truncated") if sampled else ""
        return ChatContext(
            tr("chat_system_prompt_timed", transcript=body) + suffix,
            sampled, len(lines), kept, max_chars, bool(ranked),
        )
    sampled = len(transcript) > max_chars
    return ChatContext(_build_system_prompt(transcript, max_chars), sampled, 0, (), max_chars)


def _matching_blocks(lines: list[str], question: str, budget: int) -> tuple[int, ...]:
    """Rank exact terms across the record, then add context around hits."""
    terms = list(dict.fromkeys(re.findall(r"[^\W_]{3,}", question.casefold())))[:24]
    if not terms:
        return ()
    query = " OR ".join(f'"{term}"' for term in terms)
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE blocks USING fts5(text)")
        connection.executemany(
            "INSERT INTO blocks(rowid, text) VALUES (?, ?)", enumerate(lines, 1),
        )
        matches = connection.execute(
            "SELECT rowid FROM blocks WHERE blocks MATCH ? ORDER BY rank, rowid LIMIT 32", (query,),
        ).fetchall()
    except sqlite3.OperationalError:
        return ()
    finally:
        connection.close()
    indices = [rowid - 1 for rowid, in matches]
    neighbours = [near for index in indices for near in (index - 1, index + 1)]
    selected: set[int] = set()
    for index in indices + neighbours:
        if index not in selected and 0 <= index < len(lines):
            cost = len(lines[index]) + int(bool(selected))
            if cost <= budget:
                selected.add(index)
                budget -= cost
    return tuple(sorted(selected))


def _build_system_prompt(
    transcript: str, max_chars: int = _CONTEXT_CHARS, lines: list[str] | None = None,
) -> str:
    """The chat's system prompt (see chat_context() for what it holds)."""
    if lines:
        return chat_context(transcript, max_chars, lines).prompt
    if len(transcript) > max_chars:
        transcript = transcript[:max_chars]
        suffix = "\n\n" + tr("chat_transcript_truncated")
    else:
        suffix = ""
    return tr("chat_system_prompt", transcript=transcript) + suffix


class ChatWorker(BaseWorker):
    """Streams one chat turn.

    Signals
    -------
    token_received(str)   — partial token (call on each SSE delta)
    turn_finished(str)    — full response text
    error_occurred(str)   — human-readable error
    """

    token_received = pyqtSignal(str)
    turn_finished = pyqtSignal(str)
    turn_partial = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    context_ready = pyqtSignal(object)

    def __init__(
        self,
        messages: list[dict],
        lm_url: str,
        max_context_chars: int = _CONTEXT_CHARS,
        parent=None,
        *,
        transcript: str = "",
        context_lines: list[str] | None = None,
    ):
        super().__init__(parent)
        self._messages = messages
        self._lm_url = lm_url
        self._max_context_chars = max_context_chars
        self._transcript = transcript
        self._context_lines = context_lines
        # The model LM Studio had loaded for this turn ("" when unknown);
        # read by the panel when it keeps the answer.
        self.model_name = ""

    def _on_error(self, msg: str) -> None:
        self.error_occurred.emit(msg)

    def _execute(self) -> None:
        from core.lm_client import LMStudioClient
        client = LMStudioClient(self._lm_url)
        if self._context_lines and self._messages:
            context = chat_context(
                self._transcript, self._max_context_chars, self._context_lines,
                question=self._messages[-1]["content"],
            )
            if self.is_cancelled():
                return
            self._messages[0] = {"role": "system", "content": context.prompt}
            self.context_ready.emit(context)

        def on_token(delta: str):
            self.token_received.emit(delta)

        try:
            self.model_name = client.get_loaded_model(timeout=2) or ""
        except Exception as exc:  # noqa: BLE001 - only a label for the kept answer
            logger.debug("Could not read the loaded model: %s", exc)
        result = client.chat_completion_stream(
            messages=self._messages,
            on_token=on_token,
            is_cancelled=self._cancelled.is_set,
        )

        if self._cancelled.is_set():
            return

        if not result:
            self.error_occurred.emit(
                "LM Studio is not responding.\n\n"
                "Make sure LM Studio is running and a model is loaded.\n"
                f"URL: {self._lm_url}"
            )
        elif getattr(client, "finish_reason", None) not in (None, "stop"):
            self.turn_partial.emit(result)
        else:
            self.turn_finished.emit(result)
