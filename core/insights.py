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
AGENTS.md.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Mapping, Optional, Sequence, TYPE_CHECKING

from core.glossary import glossary_block
from core.insights_cache import InsightsCache
from core.llm_text import sample_lines_evenly
from core.logger import get_logger
from core.prompts import load_prompt

if TYPE_CHECKING:
    from core.ai_provider import ProviderSettings

logger = get_logger(__name__)

_INSIGHT_TYPES = ("chapters", "action_items", "key_moments", "yt_titles", "yt_description", "yt_tags", "yt_questions", "yt_hashtags", "thumb_title")
_TRANSCRIPT_MAX_CHARS = 48_000   # ~12 k tokens; matches chat_worker._CONTEXT_CHARS
# LM Studio's DEFAULT_MAX_TOKENS (4096) truncated Cyrillic/multi-byte JSON
# responses mid-string on longer insight types (chapters, descriptions);
# non-Latin scripts tokenize far less efficiently than English.
# Reasoning models (e.g. gemma-4) spend the same budget on their hidden
# reasoning_content before emitting any visible JSON — on a dense chapters
# request over a ~48K-char transcript the old 8000 ran out mid-reasoning
# and the visible response came back empty.
_RESPONSE_MAX_TOKENS = 16_000
# Hidden reasoning a local model may spend per insight type, sent to LM
# Studio as reasoning_effort ("none" turns thinking off). A type absent
# here keeps the model's default. Cloud providers never get the field.
# Measured on a 45-minute recording with gemma-4-12b: thinking was 90% of
# the generated tokens and the YouTube package took 753 s; without it,
# 137 s, with chapters/titles/descriptions/tags/questions of the same
# quality (the chapters prompt already asks not to deliberate).
_LOCAL_REASONING_EFFORT: dict[str, str] = {name: "none" for name in _INSIGHT_TYPES}
# Types whose first answer is a draft that a second request reviews
# against the same transcript (prompts/<type>_review.md, the draft in its
# {draft} slot). On a 45-minute recording with gemma-4-12b: titles drawn as
# 12 candidates and narrowed to 5 lost the invented "3 ways to..." and got
# more specific; the description review fixed who-did-what mix-ups. Each
# costs ~10 s — the transcript is already in LM Studio's prefix cache.
_REVIEWED_TYPES = ("yt_titles", "yt_description")
# Sampling temperature per type; the rest use _DEFAULT_TEMPERATURE. Title
# candidates should differ from each other — the review picks among them.
_DEFAULT_TEMPERATURE = 0.2
_TEMPERATURE = {"yt_titles": 0.8}
_NUMBER = re.compile(r"\d+")
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


def _parse_json_response(raw: str, retry_hint: str = "", *, salvage: bool = True) -> Optional[list]:
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

    salvaged = _salvage_truncated_array(raw) if salvage else None
    if salvaged is not None:
        logger.warning(
            "Insights JSON was cut off; salvaged %d complete item(s)",
            len(salvaged),
        )
        return salvaged

    logger.warning("Failed to parse insights JSON (%d characters)", len(raw))
    return None


def _transcript_blocks(segments) -> list[tuple[int, str, str]]:
    """Coalesce consecutive same-speaker segments into roughly 25-second blocks."""
    blocks: list[tuple[int, str, str]] = []
    block_start: int | None = None
    block_speaker = ""
    block_texts: list[str] = []

    def flush() -> None:
        if block_start is not None and block_texts:
            blocks.append((block_start, block_speaker, " ".join(block_texts)))

    for seg in segments:
        start_s = int(seg.get("start", 0) if isinstance(seg, dict) else seg.start)
        text = (seg.get("text", "") if isinstance(seg, dict) else seg.text).strip()
        speaker = (seg.get("speaker") if isinstance(seg, dict) else seg.speaker) or ""
        if not text:
            continue
        if (block_start is None or speaker != block_speaker
                or start_s - block_start >= 25):
            flush()
            block_start, block_speaker, block_texts = start_s, speaker, [text]
        else:
            block_texts.append(text)
    flush()
    return blocks


def _build_prompt_text(
    insight_type: str,
    segments,
    max_transcript_chars: int = _TRANSCRIPT_MAX_CHARS,
    language: Optional[str] = None,
    notes: str = "",
    style_examples: Optional[Mapping[str, Sequence[str]]] = None,
    recipe_terms: tuple[str, ...] = (),
) -> str:
    """Build the full prompt including the timestamped transcript.

    The transcript portion is capped at *max_transcript_chars* so the total
    request stays within the model's context window. Segments are sampled
    evenly across the whole recording rather than truncating the tail, so
    a long recording's ending is still visible to the model (see
    ``core.llm_text.sample_lines_evenly``).
    If *language* is given, a directive is appended
    after the task instructions to force the output into that language.
    *notes* — the user's own notes about the record
    (application/user_notes.py) — go between the transcript and the task
    instructions, introduced by prompts/user_notes.md. The glossary terms
    the transcript uses (prompts/glossary.md) come right after it.
    *style_examples* — ``{insight type: texts}`` the host wrote or approved
    on other records (application/style_examples.py) — go after the notes
    for the types that have them, introduced by prompts/style_examples.md;
    a ``<type>_review`` uses the examples of ``<type>``.
    """
    system_prompt = load_prompt(insight_type, fallback="")
    lines = [
        f"[{start}s] " + (f"[B{index}] " if insight_type == "action_items" else "")
        + (f"{speaker}: " if speaker else "") + (" ".join(text.split()) if insight_type == "action_items" else text)
        for index, (start, speaker, text) in enumerate(_transcript_blocks(segments), 1)
    ]
    transcript = sample_lines_evenly(lines, max_transcript_chars)
    lang_directive = f"Write all output in {language}.\n" if language else ""
    # Terms the transcript uses (core/glossary.py): they depend only on
    # the transcript, so the block keeps the shared per-record prefix.
    glossary = glossary_block(transcript, recipe_terms=recipe_terms)
    glossary_section = f"{glossary}\n\n" if glossary else ""
    notes_block = ""
    if notes.strip():
        intro = load_prompt("user_notes", fallback="The user's own notes about this recording:")
        notes_block = f"{intro.strip()}\n\nUSER NOTES:\n{notes.strip()}\n\n"
    examples_block = _style_examples_block(insight_type, style_examples)
    # Transcript first, task last: every insight type of a record then
    # starts with the same tokens, so LM Studio's prompt cache reads the
    # transcript once and each further type only prefills its own
    # instructions — with the task first, all of them re-read it in full.
    return (
        _TRANSCRIPT_HEADER + "\n" + transcript + "\n\n"
        + glossary_section + notes_block + examples_block + _task_instructions(system_prompt) + "\n" + lang_directive
    )


def _style_examples_block(
    insight_type: str, style_examples: Optional[Mapping[str, Sequence[str]]],
) -> str:
    """The host's own examples for *insight_type*, or "". They differ per
    type, so they come after everything the types of a record share."""
    if not style_examples:
        return ""
    items = [str(item).strip() for item in
             style_examples.get(insight_type.removesuffix("_review")) or ()]
    items = [item for item in items if item]
    if not items:
        return ""
    intro = load_prompt("style_examples", fallback="Examples of the host's own style:")
    shown = "\n\n".join(f"Example {n}:\n{item}" for n, item in enumerate(items, 1))
    return f"{intro.strip()}\n\n{shown}\n\n"


_TRANSCRIPT_HEADER = "TRANSCRIPT (with timestamps in seconds):"
# The trailing "TRANSCRIPT:" line (or "{transcript}" slot) a prompt file
# ends with from when the transcript was appended after it.
_TRAILING_TRANSCRIPT_SLOT = re.compile(
    r"(?:\n[ \t]*(?:transcript|транскрипт)[^\n]*:[ \t]*|\n[ \t]*\{transcript\}[ \t]*|\s)+\Z",
    re.IGNORECASE,
)


def _task_instructions(system_prompt: str) -> str:
    """*system_prompt* without the transcript slot it may end with — the
    transcript now precedes the instructions (see ``_build_prompt_text``)."""
    return _TRAILING_TRANSCRIPT_SLOT.sub("", "\n" + system_prompt).strip()


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
    model: str = "",
    cache: Optional[InsightsCache] = None,
    is_cancelled: Callable[[], bool] = lambda: False,
    max_transcript_chars: Optional[int] = None,
    notes: str = "",
    style_examples: Optional[Mapping[str, Sequence[str]]] = None,
    recipe_terms: tuple[str, ...] = (),
    _chapter_window: Optional[tuple[int, Optional[int]]] = None,
    _response_tokens: int = _RESPONSE_MAX_TOKENS,
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
    style_examples : ``{insight type: texts}`` from
        ``application.style_examples`` (see ``_build_prompt_text``)

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
    if is_cancelled():
        return []

    if max_transcript_chars is None:
        from config import get_config
        max_transcript_chars = get_config().insights_context_chars

    if provider:
        from core.ai_provider import create_client
        client = create_client(provider)
    else:
        from core.lm_client import LMStudioClient
        client = LMStudioClient(lm_url)
    if provider is not None and provider.kind != "lmstudio":
        recipe_terms = ()

    selected_model = getattr(provider, "model", "") if provider else model
    if not selected_model:
        probe = getattr(client, "get_loaded_model", None)
        selected_model = probe(timeout=2) if callable(probe) else ""
    selected_model = selected_model if isinstance(selected_model, str) else ""
    if selected_model:
        client._model = selected_model

    if insight_type == "chapters" and _chapter_window is None and (
        provider is None or provider.kind == "lmstudio"
    ):
        probe_context = getattr(client, "loaded_context_length", None)
        capacity = probe_context(selected_model) if callable(probe_context) else None
        if isinstance(capacity, int):
            capacity = min(capacity, 50_000)
            _response_tokens = min(_RESPONSE_MAX_TOKENS, max(512, capacity // 3))
            # Measured Russian prose uses ~3.5 characters per token. Use a
            # smaller estimate and reserve space for instructions and terms.
            room = capacity - _response_tokens - 1_536 - len(notes.encode("utf-8"))
            if room > 256:
                budget = int(room * 2.7)
                if max_transcript_chars != _TRANSCRIPT_MAX_CHARS:
                    budget = min(max_transcript_chars, budget)
                max_transcript_chars = budget
                windows = _chapter_windows(_transcript_blocks(segments), budget)
                if len(windows) > 1:
                    from domain.timestamps import item_start

                    chapters: list[dict] = []
                    for index, window in enumerate(windows):
                        if is_cancelled():
                            return []
                        start = window[0]["start"] if index else 0
                        end = windows[index + 1][0]["start"] if index + 1 < len(windows) else None
                        items = generate_insight(
                            "chapters", window, lm_url=lm_url, language=language, provider=provider,
                            model=selected_model, cache=cache, is_cancelled=is_cancelled,
                            max_transcript_chars=budget, notes=notes, style_examples=style_examples,
                            recipe_terms=recipe_terms,
                            _chapter_window=(start, end), _response_tokens=_response_tokens,
                        )
                        if not isinstance(items, list):
                            return items
                        for item in items:
                            stamp = item_start(item) if isinstance(item, dict) else None
                            if stamp is not None and stamp >= start and (end is None or stamp < end):
                                chapters.append(item)
                    distinct = {item_start(item): item for item in chapters}
                    return sorted(distinct.values(), key=lambda item: item_start(item) or 0)

    prompt = _build_prompt_text(
        insight_type, segments, max_transcript_chars=max_transcript_chars, language=language,
        notes=notes, style_examples=style_examples,
        recipe_terms=recipe_terms,
    )
    if _chapter_window is not None:
        prompt += (
            f"Generate chapters only for this excerpt. The first chapter starts at "
            f"{_chapter_window[0]} seconds. Use the original absolute timestamps."
        )
    from core.prompts import prompt_version

    identity = json.dumps({
        "provider": provider.kind if provider else "lmstudio",
        "endpoint": (getattr(provider, "base_url", "") if provider else lm_url).rstrip("/"),
        "model": selected_model,
        "max_tokens": _response_tokens,
        "review": prompt_version(f"{insight_type}_review") if insight_type in _REVIEWED_TYPES else "",
    }, sort_keys=True)
    cache_key = cache.key(insight_type, prompt, identity) if cache and selected_model else None
    if cache is not None and cache_key is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            # Same insight type + same prompt + same provider already
            # answered this in this session (e.g. YouTube and Insights
            # both wanted "chapters") — reuse it instead of a second
            # LLM round-trip for output that would be identical anyway.
            return cached

    messages = [{"role": "user", "content": prompt}]
    # Only LM Studio understands reasoning_effort="none"; the Anthropic
    # client doesn't take the argument at all.
    effort = None if provider else _LOCAL_REASONING_EFFORT.get(insight_type)
    extra: dict = {"reasoning_effort": effort} if effort is not None else {}
    if provider is None or provider.kind != "anthropic":
        extra["partial_ok"] = False
    if insight_type == "action_items" and (provider is None or provider.kind == "lmstudio"):
        extra["response_format"] = _ACTION_FORMAT

    raw = client.chat_completion_stream(
        messages=messages,
        is_cancelled=is_cancelled,
        temperature=_TEMPERATURE.get(insight_type, _DEFAULT_TEMPERATURE),
        max_tokens=_response_tokens,
        timeout=_STREAM_TIMEOUT_S,
        **extra,
    )
    if is_cancelled():
        return []
    if raw is None:
        raise RuntimeError(_no_response_message(lm_url, provider))

    if insight_type == "thumb_title":
        from core.thumb_titles import parse_title_suggestions

        suggestions = parse_title_suggestions(raw)
        if cache is not None and cache_key is not None:
            cache.put(cache_key, suggestions)
        return suggestions

    result = _parse_json_response(raw, salvage=False)
    if result is None:
        # One retry with explicit JSON instruction
        retry_msg = messages + [
            {"role": "user", "content": (
                'Return only the JSON object with the "items" array.' if insight_type == "action_items"
                else "Return ONLY the JSON array, no other text."
            )},
        ]
        raw2 = client.chat_completion_stream(
            messages=retry_msg,
            is_cancelled=is_cancelled,
            temperature=0.1,
            max_tokens=_response_tokens,
            timeout=_STREAM_TIMEOUT_S,
            **extra,
        )
        if is_cancelled():
            return []
        if raw2:
            result = _parse_json_response(raw2, salvage=False)
        if result is None:
            # Fall back: return the raw text for the caller to display as-is
            return raw

    if insight_type == "action_items":
        result = _ground_actions(result, _transcript_blocks(segments), prompt)

    if insight_type in _REVIEWED_TYPES and result:
        result = _review(
            insight_type, result, segments, client=client, extra=extra,
            is_cancelled=is_cancelled, max_transcript_chars=max_transcript_chars,
            language=language, notes=notes, style_examples=style_examples,
            recipe_terms=recipe_terms,
        )
    if insight_type == "yt_titles":
        result = _drop_unsaid_numbers(result, segments)

    if is_cancelled():
        return []
    if cache is not None and cache_key is not None:
        cache.put(cache_key, result)
    return result


def _chapter_windows(blocks: list[tuple[int, str, str]], budget: int) -> list[list[dict]]:
    """Partition full timed blocks without changing the recording's clock."""
    windows: list[list[dict]] = []
    window: list[dict] = []
    used = 0
    for start, speaker, text in blocks:
        length = len(f"[{start}s] ") + (len(speaker) + 2 if speaker else 0) + len(text) + 1
        if window and window[-1]["start"] != start and used + length > budget:
            windows.append(window)
            window = []
            used = 0
        window.append({"start": start, "speaker": speaker, "text": text})
        used += length
    if window:
        windows.append(window)
    return windows


_ACTION_FIELDS = {
    "task": {"type": "string"},
    "block_id": {"type": "string"},
    "quote": {"type": "string"},
    "owner": {"type": ["string", "null"]},
    "deadline": {"type": ["string", "null"]},
}
_ACTION_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "action_items",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {
                "type": "object",
                "properties": _ACTION_FIELDS,
                "required": list(_ACTION_FIELDS),
                "additionalProperties": False,
            }}},
            "required": ["items"],
            "additionalProperties": False,
        },
    },
}


def _ground_actions(
    items: list, blocks: list[tuple[int, str, str]], prompt: str,
) -> list[dict]:
    """Keep actions with a verbatim quote from an included block."""
    grounded = []
    shown_lines = set(prompt.splitlines())
    for item in items:
        if not isinstance(item, dict):
            continue
        block_id = item.get("block_id")
        if not isinstance(block_id, str) or not re.fullmatch(r"B[1-9][0-9]{0,8}", block_id):
            continue
        index = int(block_id[1:]) - 1
        if index >= len(blocks):
            continue
        start, speaker, text = blocks[index]
        # Test membership in the exact prompt, including the quote. A sampled
        # or clipped block cannot support an action the model did not see.
        text = " ".join(text.split())
        line = f"[{start}s] [{block_id}] " + (f"{speaker}: " if speaker else "") + text
        if line not in shown_lines:
            continue
        quote, task = item.get("quote"), item.get("task")
        if not isinstance(quote, str) or not isinstance(task, str) or not task.strip():
            continue
        quote = " ".join(quote.split())
        if not quote or quote not in text:
            continue
        result = {"task": task.strip(), "start": start, "quote": quote}
        for field in ("owner", "deadline"):
            value = item.get(field)
            value = " ".join(value.split()) if isinstance(value, str) else ""
            # Match entire words so an invented name cannot match a prefix.
            result[field] = value if value and re.search(
                rf"(?<!\w){re.escape(value)}(?!\w)", quote, re.IGNORECASE,
            ) else None
        grounded.append(result)
    return grounded


def _review(
    insight_type: str,
    draft: list,
    segments,
    *,
    client,
    extra: dict,
    is_cancelled: Callable[[], bool],
    max_transcript_chars: int,
    language: Optional[str],
    notes: str,
    style_examples: Optional[Mapping[str, Sequence[str]]] = None,
    recipe_terms: tuple[str, ...] = (),
) -> list:
    """Second pass over *draft* with prompts/<insight_type>_review.md:
    the reviewed list, or *draft* itself when there is no review prompt or
    the review came back empty or unparseable — a review may improve a
    result but must never lose it."""
    review_type = f"{insight_type}_review"
    if not load_prompt(review_type, fallback=""):
        return draft
    items = [str(item) for item in draft]
    shown = items[0] if len(items) == 1 else "\n".join(f"- {item}" for item in items)
    prompt = _build_prompt_text(
        review_type, segments, max_transcript_chars=max_transcript_chars,
        language=language, notes=notes, style_examples=style_examples,
        recipe_terms=recipe_terms,
    ).replace("{draft}", shown)
    raw = client.chat_completion_stream(
        messages=[{"role": "user", "content": prompt}],
        is_cancelled=is_cancelled,
        temperature=_DEFAULT_TEMPERATURE,
        max_tokens=_RESPONSE_MAX_TOKENS,
        timeout=_STREAM_TIMEOUT_S,
        **extra,
    )
    reviewed = _parse_json_response(raw, salvage=False) if raw and not is_cancelled() else None
    if not reviewed or not all(isinstance(item, str) and item.strip() for item in reviewed):
        logger.warning("%s review gave no usable answer; keeping the draft", insight_type)
        return draft
    if len(draft) == 1 and len(reviewed) > 1:
        # A one-string answer (the description) sometimes comes back split
        # into hook and paragraph; consumers read only its first item.
        return ["\n\n".join(item.strip() for item in reviewed)]
    return reviewed


def _drop_unsaid_numbers(titles, segments):
    """*titles* without those quoting a number nobody said ("3 ways to…"
    in a talk with no three ways) — unless that would drop them all."""
    if not isinstance(titles, list):
        return titles
    said = set(_NUMBER.findall(" ".join(
        (seg.get("text", "") if isinstance(seg, dict) else seg.text) for seg in segments
    )))
    kept = [t for t in titles if set(_NUMBER.findall(str(t))) <= said]
    if len(kept) < len(titles):
        logger.info("Dropped %d title(s) with numbers not in the transcript", len(titles) - len(kept))
    return kept or titles
