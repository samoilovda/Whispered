"""Synchronous insight generation.

This is the actual LLM call and JSON-parsing/salvage logic behind
``InsightsWorker`` (core/insights_worker.py), pulled out so a caller that
isn't a QThread — the "insights"/"youtube_package" job steps in
application/steps.py — can call it directly (see
docs/UI_REDESIGN_PLAN_2026-09.ru.md, B0). ``InsightsWorker._execute()``
becomes a thin wrapper: call ``generate_insight()``, emit its result as
the worker's ``finished`` signal, and let ``BaseWorker.run()``'s existing
try/except route a raised error to ``_on_error()``. Behavior and every
public name InsightsWorker used to define at module level are unchanged —
core/insights_worker.py re-exports them from here (same pattern
transcriber.py uses for the domain DTOs) so nothing importing
``core.insights_worker._build_prompt_text`` etc. needs to change.

Qt-free: no PyQt import here, matching the domain/application layering in
CLAUDE.md's architecture map.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional, TYPE_CHECKING

from core.insights_cache import InsightsCache
from core.llm_text import sample_lines_evenly
from core.logger import get_logger
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


def _as_list(value) -> Optional[list]:
    """Coerce a parsed JSON value to the list of items callers expect.

    Every insight prompt asks for a top-level array, but models routinely
    wrap it in an object instead (``{"chapters": [...]}``). That parses
    fine, so the old code handed the dict straight back as the result and
    the panel rendered its *keys* as insights. Unwrap the single list this
    kind of envelope carries; anything else is a parse failure, which
    routes the caller into its existing "ask again for just the JSON
    array" retry.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        lists = [item for item in value.values() if isinstance(item, list)]
        if len(lists) == 1:
            return lists[0]
    return None


def _parse_json_response(raw: str, retry_hint: str = "") -> Optional[list]:
    """Try to parse a JSON array from a raw LLM response.

    If parsing fails once, a second attempt is made with the retry_hint
    injected (not used in this implementation — caller may retry).
    Returns a list or None on failure.
    """
    try:
        parsed = _as_list(json.loads(_strip_json_fences(raw)))
        if parsed is not None:
            return parsed
    except json.JSONDecodeError:
        pass

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


def _no_response_message(lm_url: str, provider: Optional["ProviderSettings"]) -> str:
    """Human-readable "no response" message naming the actual provider in
    use, instead of always blaming LM Studio even when the request went to
    a cloud provider."""
    if provider is None:
        return f"LM Studio did not respond ({lm_url})."
    labels = {"openai": "OpenAI-compatible API", "anthropic": "Anthropic"}
    label = labels.get(provider.kind, provider.kind)
    return f"{label} did not respond."


def generate_insight(
    insight_type: str,
    segments,
    *,
    lm_url: str,
    language: Optional[str] = None,
    provider: Optional["ProviderSettings"] = None,
    cache: Optional[InsightsCache] = None,
    is_cancelled: Callable[[], bool] = lambda: False,
    max_transcript_chars: Optional[int] = None,
):
    """Generate one insight type from a transcript, synchronously.

    Parameters
    ----------
    insight_type : one of ``_INSIGHT_TYPES``
    segments     : list of Segment objects or dicts with start/text/speaker
    lm_url       : LM Studio base URL (used when *provider* is None)
    max_transcript_chars : defaults to ``Config.insights_context_chars``
        when not given — callers with no config context (e.g. tests) may
        pass it explicitly.

    Returns the parsed insight list, a list of suggestions (thumb_title),
    the raw response text if it never parsed as JSON after a retry, or
    ``[]`` if *is_cancelled* became true mid-call — matching
    InsightsWorker's prior ``finished.emit(type, [])`` on cancellation so
    a pending-counter caller can decrement cleanly either way.

    Raises
    ------
    ValueError
        *insight_type* isn't one of ``_INSIGHT_TYPES``.
    RuntimeError
        The provider never responded. A QThread caller
        (``InsightsWorker``) lets ``BaseWorker.run()``'s try/except route
        this to its error signal; a synchronous caller (a job step) should
        catch it directly.
    """
    if insight_type not in _INSIGHT_TYPES:
        raise ValueError(f"Unknown insight type: {insight_type}")

    if max_transcript_chars is None:
        from config import get_config
        max_transcript_chars = getattr(
            get_config(), "insights_context_chars", _TRANSCRIPT_MAX_CHARS
        )

    prompt = _build_prompt_text(
        insight_type, segments, max_transcript_chars=max_transcript_chars, language=language
    )

    provider_id = provider.kind if provider else "lmstudio"
    cache_key = cache.key(insight_type, prompt, provider_id) if cache else None
    if cache is not None and cache_key is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            # Same insight type + same prompt + same provider already
            # answered this in this session (e.g. YouTube and Insights
            # both wanted "chapters") — reuse it instead of a second
            # LLM round-trip for output that would be identical anyway.
            return cached

    if provider:
        from core.ai_provider import create_client
        client = create_client(provider)
    else:
        from core.lm_client import LMStudioClient
        client = LMStudioClient(lm_url)

    messages = [{"role": "user", "content": prompt}]

    raw = client.chat_completion_stream(
        messages=messages,
        is_cancelled=is_cancelled,
        temperature=0.2,
        max_tokens=_RESPONSE_MAX_TOKENS,
        timeout=_STREAM_TIMEOUT_S,
    )
    if is_cancelled():
        return []
    if raw is None:
        raise RuntimeError(_no_response_message(lm_url, provider))

    if insight_type == "thumb_title":
        from covers.title import parse_title_suggestions

        suggestions = parse_title_suggestions(raw)
        if cache is not None and cache_key is not None:
            cache.put(cache_key, suggestions)
        return suggestions

    result = _parse_json_response(raw)
    if result is None:
        # One retry with explicit JSON instruction
        retry_msg = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "Return ONLY the JSON array, no other text."},
        ]
        raw2 = client.chat_completion_stream(
            messages=retry_msg,
            is_cancelled=is_cancelled,
            temperature=0.1,
            max_tokens=_RESPONSE_MAX_TOKENS,
            timeout=_STREAM_TIMEOUT_S,
        )
        if raw2:
            result = _parse_json_response(raw2)
        if result is None:
            # Fall back: return the raw text for the caller to display as-is
            if cache is not None and cache_key is not None:
                cache.put(cache_key, raw)
            return raw

    if cache is not None and cache_key is not None:
        cache.put(cache_key, result)
    return result
