"""
Whispered – AI Chat Worker
QThread that sends a user message to LM Studio with the transcript
as system context and streams tokens back to the UI.
"""

from __future__ import annotations


from PyQt6.QtCore import pyqtSignal

from core.base_worker import BaseWorker
from core.logger import get_logger

logger = get_logger(__name__)

_CONTEXT_CHARS = 48_000   # default: ~ 12 k tokens

_SYSTEM_TEMPLATE = (
    "You are a helpful assistant. Answer questions ONLY based on the "
    "transcription provided below. If the answer is not in the transcription, "
    "say so clearly.\n\nTRANSCRIPTION:\n{transcript}"
)


def _build_system_prompt(transcript: str, max_chars: int = _CONTEXT_CHARS) -> str:
    if len(transcript) > max_chars:
        transcript = transcript[:max_chars]
        suffix = "\n\n[Transcript truncated due to context limit]"
    else:
        suffix = ""
    return _SYSTEM_TEMPLATE.format(transcript=transcript) + suffix


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
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        messages: list[dict],
        lm_url: str,
        max_context_chars: int = _CONTEXT_CHARS,
        parent=None,
    ):
        super().__init__(parent)
        self._messages = messages
        self._lm_url = lm_url
        self._max_context_chars = max_context_chars

    def _on_error(self, msg: str) -> None:
        self.error_occurred.emit(msg)

    def _execute(self) -> None:
        from core.lm_client import LMStudioClient
        client = LMStudioClient(self._lm_url)

        def on_token(delta: str):
            self.token_received.emit(delta)

        result = client.chat_completion_stream(
            messages=self._messages,
            on_token=on_token,
            is_cancelled=self._cancelled.is_set,
        )

        if self._cancelled.is_set():
            return

        if result is None:
            self.error_occurred.emit(
                "LM Studio is not responding.\n\n"
                "Make sure LM Studio is running and a model is loaded.\n"
                f"URL: {self._lm_url}"
            )
        else:
            self.turn_finished.emit(result)
