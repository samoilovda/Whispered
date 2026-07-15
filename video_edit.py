"""Whispered - Video Edit: algorithmic and LLM-assisted segment cleanup.

mark_pauses() — pure algorithm, no dependencies.
LLMFillerWorker — optional LLM pass via existing LMStudioClient infrastructure.
"""

from __future__ import annotations

import re

from core.logger import get_logger

logger = get_logger(__name__)

# Filler words / phrases (lower-case, stripped of punctuation) that mark a
# segment as likely unwanted when it contains only these tokens.
_FILLER_WORDS: frozenset[str] = frozenset({
    # English
    "um", "uh", "uhm", "er", "ah", "hmm", "hm", "mhm", "mm",
    "like", "right", "okay", "ok", "so", "well", "actually",
    # Russian
    "эм", "ну", "вот", "это", "значит", "типа", "короче",
    "ладно", "хорошо", "понятно", "собственно", "буквально",
})


def mark_pauses(
    segments,
    min_duration: float = 0.5,
    gap_threshold: float = 0.0,
) -> list[int]:
    """Return indices of segments that are likely pauses or filler-only.

    A segment is marked when ANY of:
    - its duration < min_duration seconds, OR
    - its text (after stripping punctuation) consists entirely of filler words.

    Optionally, if gap_threshold > 0, also mark segments that are preceded by
    a silence gap longer than gap_threshold (the gap itself is silence, but the
    segment immediately after a long pause is often a fresh thought — callers
    may prefer to use this to mark the *preceding* segment instead, depending
    on workflow).

    Parameters
    ----------
    segments      : Sequence of objects with .start, .end, .text attributes.
    min_duration  : Segments shorter than this (seconds) are marked.
    gap_threshold : If > 0, mark segments where (seg.start - prev.end) >
                    gap_threshold. Disabled by default (0.0 = off).
    """
    indices: list[int] = []
    prev_end: float = 0.0

    for i, seg in enumerate(segments):
        duration = seg.end - seg.start
        words = re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", seg.text.lower())

        is_short = duration < min_duration
        is_filler = bool(words) and all(w in _FILLER_WORDS for w in words)
        is_after_long_gap = (
            gap_threshold > 0
            and i > 0
            and (seg.start - prev_end) > gap_threshold
        )

        if is_short or is_filler or is_after_long_gap:
            indices.append(i)

        prev_end = seg.end

    return indices


# ---------------------------------------------------------------------------
# LLM-assisted filler detection (optional, requires LM Studio running)
# ---------------------------------------------------------------------------



_FILLER_PROMPT_FALLBACK = """\
You are a video editor assistant. Below is a transcript with timestamps.
Identify segments that should be CUT: repeated words, false starts, "um/uh/эм/ну",
long pauses, off-topic tangents, or botched takes.

Return ONLY a JSON array of start times (in seconds, as numbers) for segments to cut.
Example: [0.0, 12.5, 47.3]

If nothing should be cut, return: []

Transcript:
"""



def times_to_indices(start_times: list[float], segments, tolerance: float = 1.0) -> list[int]:
    """Map a list of start times (from LLM) to segment indices.

    For each time, picks the segment whose .start is within `tolerance` seconds.
    """
    indices = []
    for t in start_times:
        best_idx = None
        best_dist = float("inf")
        for i, seg in enumerate(segments):
            d = abs(seg.start - t)
            if d < best_dist and d <= tolerance:
                best_dist = d
                best_idx = i
        if best_idx is not None and best_idx not in indices:
            indices.append(best_idx)
    return sorted(indices)
