"""
Whispered – Insights Worker
Generates chapters, action items, and key moments from a transcript
using a local LM Studio model.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from PyQt6.QtCore import pyqtSignal

from core.base_worker import BaseWorker
from core.logger import get_logger
from core.llm_text import fit_to_context
from core.prompts import load_prompt

logger = get_logger(__name__)

_INSIGHT_TYPES = ("chapters", "action_items", "key_moments", "yt_titles", "yt_description", "yt_tags")
_TRANSCRIPT_MAX_CHARS = 48_000   # ~12 k tokens; matches chat_worker._CONTEXT_CHARS


def _strip_json_fences(text: str) -> str:
    """Remove leading/trailing markdown code fences if present."""
    text = text.strip()
    text = re.sub(r'^```[a-z]*\n?', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n?```$', '', text, flags=re.MULTILINE)
    return text.strip()


def _parse_json_response(raw: str, retry_hint: str = "") -> Optional[list]:
    """Try to parse a JSON array from a raw LLM response.

    If parsing fails once, a second attempt is made with the retry_hint
    injected (not used in this implementation — caller may retry).
    Returns a list or None on failure.
    """
    try:
        return json.loads(_strip_json_fences(raw))
    except json.JSONDecodeError:
        # Try extracting the first [...] block
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        logger.warning("Failed to parse insights JSON: %.80s", raw)
        return None


def _build_prompt_text(
    insight_type: str,
    segments,
    max_transcript_chars: int = _TRANSCRIPT_MAX_CHARS,
    language: Optional[str] = None,
) -> str:
    """Build the full prompt including the timestamped transcript.

    The transcript portion is capped at *max_transcript_chars* so the total
    request stays within the model's context window.
    If *language* is given, a directive is inserted after the system prompt
    to force chapter titles into that language.
    """
    system_prompt = load_prompt(insight_type, fallback="")
    lines = []
    for seg in segments:
        start_s = int(seg.get("start", 0) if isinstance(seg, dict) else seg.start)
        text = (seg.get("text", "") if isinstance(seg, dict) else seg.text).strip()
        speaker = (seg.get("speaker") if isinstance(seg, dict) else seg.speaker) or ""
        prefix = f"[{start_s}s] "
        if speaker:
            prefix += f"{speaker}: "
        lines.append(f"{prefix}{text}")
    transcript = fit_to_context("\n".join(lines), max_transcript_chars)
    lang_directive = f"Write all output in {language}.\n" if language else ""
    return system_prompt + "\n" + lang_directive + transcript


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

    def __init__(self, insight_type: str, segments, lm_url: str,
                 language: Optional[str] = None, parent=None):
        super().__init__(parent)
        if insight_type not in _INSIGHT_TYPES:
            raise ValueError(f"Unknown insight type: {insight_type}")
        self._type = insight_type
        self._segments = segments
        self._lm_url = lm_url
        self._language = language

    def _on_error(self, msg: str) -> None:
        self.error_occurred.emit(self._type, msg)

    def _execute(self):
        from core.lm_client import LMStudioClient
        client = LMStudioClient(self._lm_url)

        prompt = _build_prompt_text(self._type, self._segments, language=self._language)
        messages = [{"role": "user", "content": prompt}]

        raw = client.chat_completion_stream(
            messages=messages,
            is_cancelled=self._cancelled.is_set,
            temperature=0.2,
        )
        if self._cancelled.is_set():
            # Emit empty list so InsightsPanel can decrement _pending cleanly
            self.finished.emit(self._type, [])
            return
        if raw is None:
            self.error_occurred.emit(
                self._type,
                f"LM Studio did not respond ({self._lm_url})."
            )
            return

        result = _parse_json_response(raw)
        if result is None:
            # One retry with explicit JSON instruction
            retry_msg = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "Return ONLY the JSON array, no other text."},
            ]
            raw2 = client.chat_completion_stream(
                messages=retry_msg,
                is_cancelled=self._cancelled.is_set,
                temperature=0.1,
            )
            if raw2:
                result = _parse_json_response(raw2)
            if result is None:
                # Fall back: emit the raw text for the UI to display as-is
                self.finished.emit(self._type, raw)
                return

        self.finished.emit(self._type, result)
