"""
Whispered – Insights Worker
Generates chapters, action items, and key moments from a transcript
using a local LM Studio model.
"""

from __future__ import annotations

import json
import re
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal

from core.base_worker import BaseWorker
from core.insights_cache import InsightsCache
from core.logger import get_logger
from core.llm_text import sample_lines_evenly
from core.prompts import load_prompt

if TYPE_CHECKING:
    from core.ai_provider import ProviderSettings

logger = get_logger(__name__)

_INSIGHT_TYPES = ("chapters", "action_items", "key_moments", "yt_titles", "yt_description", "yt_tags", "yt_questions", "thumb_title")
_TRANSCRIPT_MAX_CHARS = 48_000   # ~12 k tokens; matches chat_worker._CONTEXT_CHARS
# LM Studio's DEFAULT_MAX_TOKENS (4096) truncated Cyrillic/multi-byte JSON
# responses mid-string on longer insight types (chapters, descriptions);
# non-Latin scripts tokenize far less efficiently than English.
# Reasoning models (e.g. gemma-4) spend the same budget on their hidden
# reasoning_content before emitting any visible JSON — on a dense chapters
# request over a ~48K-char transcript the old 8000 ran out mid-reasoning
# and the visible response came back empty.
_RESPONSE_MAX_TOKENS = 16_000
# Socket-read timeout per stream. The client default (300s) fired on long
# prefills/reasoning stretches where LM Studio sends no content deltas.
_STREAM_TIMEOUT_S = 600


def _strip_json_fences(text: str) -> str:
    """Remove leading/trailing markdown code fences if present."""
    text = text.strip()
    text = re.sub(r'^```[a-z]*\n?', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n?```$', '', text, flags=re.MULTILINE)
    return text.strip()


def _salvage_truncated_array(raw: str) -> Optional[list]:
    """Recover the complete elements of a cut-off JSON array.

    A local reasoning model that spends its ``max_tokens`` budget mid-array
    leaves valid elements followed by a half-written one and no closing
    ``]`` — so both plain parsing and the ``[...]`` regex fail and every
    insight the model *did* finish is thrown away.  Decoding element by
    element and stopping at the first incomplete one keeps them.

    Returns the salvaged elements, or None if nothing usable was found.
    """
    start = raw.find("[")
    if start == -1:
        return None
    decoder = json.JSONDecoder()
    items: list = []
    idx = start + 1
    while True:
        while idx < len(raw) and raw[idx] in " \t\r\n,":
            idx += 1
        if idx >= len(raw) or raw[idx] == "]":
            break
        try:
            value, idx = decoder.raw_decode(raw, idx)
        except ValueError:
            break  # trailing partial element — drop just that one
        items.append(value)
    return items or None


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
        salvaged = _salvage_truncated_array(raw)
        if salvaged is not None:
            logger.warning(
                "Insights JSON was cut off; salvaged %d complete item(s)",
                len(salvaged),
            )
            return salvaged
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
    request stays within the model's context window. Segments are sampled
    evenly across the whole recording rather than truncating the tail, so
    a long recording's ending is still visible to the model (see
    ``core.llm_text.sample_lines_evenly``).
    If *language* is given, a directive is inserted after the system prompt
    to force chapter titles into that language.
    """
    system_prompt = load_prompt(insight_type, fallback="")
    lines = []
    # Coalesce consecutive same-speaker segments into ~25s blocks: whisper
    # emits thousands of sub-second segments, and one "[Ns] " prefix per
    # segment bloats the prompt (slow prefill) and drowns a reasoning
    # model in timestamps. One marker per block keeps every word of the
    # transcript while cutting the line count from thousands to ~150; 25s
    # resolution is far finer than any chapter/insight needs.
    block_start: int | None = None
    block_speaker = ""
    block_texts: list[str] = []

    def _flush():
        if block_texts:
            prefix = f"[{block_start}s] "
            if block_speaker:
                prefix += f"{block_speaker}: "
            lines.append(prefix + " ".join(block_texts))

    for seg in segments:
        start_s = int(seg.get("start", 0) if isinstance(seg, dict) else seg.start)
        text = (seg.get("text", "") if isinstance(seg, dict) else seg.text).strip()
        speaker = (seg.get("speaker") if isinstance(seg, dict) else seg.speaker) or ""
        if not text:
            continue
        if (block_start is None or speaker != block_speaker
                or start_s - block_start >= 25):
            _flush()
            block_start, block_speaker, block_texts = start_s, speaker, [text]
        else:
            block_texts.append(text)
    _flush()
    transcript = sample_lines_evenly(lines, max_transcript_chars)
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
        if self._provider is None:
            return f"LM Studio did not respond ({self._lm_url})."
        labels = {"openai": "OpenAI-compatible API", "anthropic": "Anthropic"}
        label = labels.get(self._provider.kind, self._provider.kind)
        return f"{label} did not respond."

    def _execute(self):
        from config import get_config
        max_chars = getattr(get_config(), "insights_context_chars", _TRANSCRIPT_MAX_CHARS)
        prompt = _build_prompt_text(
            self._type, self._segments, max_transcript_chars=max_chars, language=self._language
        )

        provider_id = self._provider.kind if self._provider else "lmstudio"
        cache_key = self._cache.key(self._type, prompt, provider_id) if self._cache else None
        if cache_key is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                # Same insight type + same prompt + same provider already
                # answered this in this session (e.g. YouTube and Insights
                # both wanted "chapters") — reuse it instead of a second
                # LLM round-trip for output that would be identical anyway.
                self.finished.emit(self._type, cached)
                return

        if self._provider:
            from core.ai_provider import create_client
            client = create_client(self._provider)
        else:
            from core.lm_client import LMStudioClient
            client = LMStudioClient(self._lm_url)

        messages = [{"role": "user", "content": prompt}]

        raw = client.chat_completion_stream(
            messages=messages,
            is_cancelled=self._cancelled.is_set,
            temperature=0.2,
            max_tokens=_RESPONSE_MAX_TOKENS,
            timeout=_STREAM_TIMEOUT_S,
        )
        if self._cancelled.is_set():
            # Emit empty list so InsightsPanel can decrement _pending cleanly
            self.finished.emit(self._type, [])
            return
        if raw is None:
            self.error_occurred.emit(self._type, self._no_response_message())
            return

        if self._type == "thumb_title":
            from covers.title import parse_title_suggestions

            suggestions = parse_title_suggestions(raw)
            if cache_key is not None:
                self._cache.put(cache_key, suggestions)
            self.finished.emit(self._type, suggestions)
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
                max_tokens=_RESPONSE_MAX_TOKENS,
                timeout=_STREAM_TIMEOUT_S,
            )
            if raw2:
                result = _parse_json_response(raw2)
            if result is None:
                # Fall back: emit the raw text for the UI to display as-is
                if cache_key is not None:
                    self._cache.put(cache_key, raw)
                self.finished.emit(self._type, raw)
                return

        if cache_key is not None:
            self._cache.put(cache_key, result)
        self.finished.emit(self._type, result)
