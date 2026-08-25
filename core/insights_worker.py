"""
Whispered – Insights Worker
Generates chapters, action items, and key moments from a transcript
using a local LM Studio model.

The actual LLM call/JSON-handling logic lives in core/insights.py as a
synchronous ``generate_insight()`` (see docs/UI_REDESIGN_PLAN_2026-09.ru.md,
B0) so it can be called directly from a job step's runner, off a QThread.
Everything this module used to define at module level is re-exported from
there — the same pattern transcriber.py uses for the domain DTOs — so
nothing importing e.g. ``core.insights_worker._build_prompt_text`` needs
to change.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal

from core.base_worker import BaseWorker
from core.insights import (
    _INSIGHT_TYPES,
    _RESPONSE_MAX_TOKENS,
    _STREAM_TIMEOUT_S,
    _TRANSCRIPT_MAX_CHARS,
    _build_prompt_text,
    _no_response_message,
    _parse_json_response,
    _salvage_truncated_array,
    _strip_json_fences,
    generate_insight,
)
from core.insights_cache import InsightsCache

if TYPE_CHECKING:
    from core.ai_provider import ProviderSettings

__all__ = [
    "InsightsWorker",
    "_INSIGHT_TYPES",
    "_RESPONSE_MAX_TOKENS",
    "_STREAM_TIMEOUT_S",
    "_TRANSCRIPT_MAX_CHARS",
    "_build_prompt_text",
    "_parse_json_response",
    "_salvage_truncated_array",
    "_strip_json_fences",
]


class InsightsWorker(BaseWorker):
    """Generate one insight type from a transcript.

    Parameters
    ----------
    insight_type : "chapters" | "action_items" | "key_moments"
    segments     : list of Segment objects or dicts with start/text/speaker
    lm_url       : LM Studio base URL
    """

    finished = pyqtSignal(str, object)   # (insight_type, list_or_None)
    error_occurred = pyqtSignal(str, str)  # (insight_type, message)

    def _disconnect_business_signals(self) -> None:
        """WorkerRegistry hook (see core/worker_registry.py).

        The registry's generic by-name sweep deliberately skips any signal
        named ``finished`` so it never touches QThread's own lifecycle
        signal — but this class's ``finished`` is a business signal that
        happens to shadow it. Disconnect both explicitly instead.
        """
        for signal in (self.finished, self.error_occurred):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass

    def __init__(self, insight_type: str, segments, lm_url: str,
                 language: Optional[str] = None,
                 provider: Optional["ProviderSettings"] = None, parent=None,
                 cache: Optional[InsightsCache] = None):
        super().__init__(parent)
        if insight_type not in _INSIGHT_TYPES:
            raise ValueError(f"Unknown insight type: {insight_type}")
        self._type = insight_type
        self._segments = segments
        self._lm_url = lm_url
        self._language = language
        self._provider = provider
        self._cache = cache

    def _on_error(self, msg: str) -> None:
        self.error_occurred.emit(self._type, msg)

    def _no_response_message(self) -> str:
        """Human-readable "no response" message naming the actual provider
        in use, instead of always blaming LM Studio even when the request
        went to a cloud provider."""
        return _no_response_message(self._lm_url, self._provider)

    def _execute(self):
        # generate_insight() raises RuntimeError on "provider never
        # responded" — BaseWorker.run()'s try/except catches it and calls
        # _on_error() above, same error signal/message this worker always
        # emitted for that case.
        result = generate_insight(
            self._type,
            self._segments,
            lm_url=self._lm_url,
            language=self._language,
            provider=self._provider,
            cache=self._cache,
            is_cancelled=self._cancelled.is_set,
        )
        self.finished.emit(self._type, result)
