"""Quality metrics for speech recognition output (Qt-free, pure functions).

Used by ``tools/bench_asr.py`` to compare transcription configurations on
recordings with a reference transcript: word/character error rate,
hallucinations in silence, stuck repeats and word-boundary accuracy.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass
from typing import Optional, Sequence

_NON_WORD = re.compile(r"[^\w\s]|_", re.UNICODE)

# A reference gap this long counts as "the speaker is silent"; whatever the
# model writes inside it is a hallucination.
SILENCE_MIN_SECONDS = 2.0


@dataclass(frozen=True)
class TimedWord:
    """A word with its position in the recording."""
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TimedSpan:
    """A transcript segment, optionally with word timings."""
    start: float
    end: float
    text: str
    words: tuple[TimedWord, ...] = ()


def normalize_words(text: str) -> list[str]:
    """Lower-case words without punctuation; ё is folded into е."""
    cleaned = _NON_WORD.sub(" ", text.lower().replace("ё", "е"))
    return cleaned.split()


def _edit_distance(ref: Sequence[str], hyp: Sequence[str]) -> int:
    """Levenshtein distance between two sequences (rolling row)."""
    if not ref:
        return len(hyp)
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i]
        for j, h in enumerate(hyp, 1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (r != h),
            ))
        previous = current
    return previous[-1]


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate; 0.0 for two empty texts, the hypothesis length
    (as a share of 1 word) when only the reference is empty."""
    ref = normalize_words(reference)
    hyp = normalize_words(hypothesis)
    if not ref:
        return float(len(hyp))
    return _edit_distance(ref, hyp) / len(ref)


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate over the normalized text, spaces included."""
    ref = " ".join(normalize_words(reference))
    hyp = " ".join(normalize_words(hypothesis))
    if not ref:
        return float(len(hyp))
    return _edit_distance(ref, hyp) / len(ref)


def silence_intervals(
    ref_words: Sequence[TimedWord],
    duration: float,
    min_length: float = SILENCE_MIN_SECONDS,
) -> list[tuple[float, float]]:
    """Stretches of at least ``min_length`` seconds with no reference word."""
    gaps: list[tuple[float, float]] = []
    covered = 0.0
    for w in sorted(ref_words, key=lambda x: x.start):
        if w.start - covered >= min_length:
            gaps.append((covered, w.start))
        covered = max(covered, w.end)
    if duration - covered >= min_length:
        gaps.append((covered, duration))
    return gaps


def _span_words(span: TimedSpan) -> list[TimedWord]:
    """Words of a span with timings; spread evenly over the span when it
    has only text."""
    if span.words:
        return list(span.words)
    tokens = span.text.split()
    if not tokens:
        return []
    step = (span.end - span.start) / len(tokens)
    return [
        TimedWord(span.start + i * step, span.start + (i + 1) * step, tok)
        for i, tok in enumerate(tokens)
    ]


def silence_hallucination_words(
    spans: Sequence[TimedSpan],
    silences: Sequence[tuple[float, float]],
) -> int:
    """Hypothesis words whose midpoint falls in a reference silence."""
    count = 0
    for span in spans:
        for w in _span_words(span):
            if not normalize_words(w.text):
                continue
            mid = (w.start + w.end) / 2
            if any(lo <= mid <= hi for lo, hi in silences):
                count += 1
    return count


def repeated_words(words: Sequence[str]) -> int:
    """Words that are excess repeats of the same 1-4-word group.

    A group counts when it appears 3 times in a row (4 for a single word:
    "да, да, да" is speech, "да да да да" is a loop). Everything beyond the
    first occurrence is counted.
    """
    total = 0
    i = 0
    n_words = len(words)
    while i < n_words:
        best = 0
        best_n = 0
        for n in range(1, 5):
            reps = 1
            while (i + (reps + 1) * n <= n_words
                   and list(words[i + reps * n:i + (reps + 1) * n]) == list(words[i:i + n])):
                reps += 1
            needed = 4 if n == 1 else 3
            if reps >= needed and reps * n > best:
                best = reps * n
                best_n = n
        if best:
            total += best - best_n
            i += best
        else:
            i += 1
    return total


def repeat_count(spans: Sequence[TimedSpan]) -> int:
    """Excess repeated words in the whole hypothesis, plus the excess of
    identical consecutive segments (3+ in a row)."""
    all_words: list[str] = []
    for span in spans:
        all_words.extend(normalize_words(span.text))
    total = repeated_words(all_words)
    run = 1
    texts = [" ".join(normalize_words(s.text)) for s in spans]
    for prev, cur in zip(texts, texts[1:] + [None]):
        if cur is not None and cur and cur == prev:
            run += 1
            continue
        if run >= 3:
            total += run - 1
        run = 1
    return total


@dataclass(frozen=True)
class BoundaryError:
    """Word-boundary error against a reference with timings (seconds)."""
    matched: int
    mean: float
    p90: float


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def boundary_error(
    ref_words: Sequence[TimedWord],
    hyp_words: Sequence[TimedWord],
) -> Optional[BoundaryError]:
    """Mean and p90 of the start/end error over words both sides agree on.

    Words are paired by aligning the normalized texts; each pair
    contributes the mean of its start and end deviation. None when no
    word pairs up.
    """
    ref = [(" ".join(normalize_words(w.text)), w) for w in ref_words]
    hyp = [(" ".join(normalize_words(w.text)), w) for w in hyp_words]
    ref = [(t, w) for t, w in ref if t]
    hyp = [(t, w) for t, w in hyp if t]
    matcher = difflib.SequenceMatcher(
        None, [t for t, _ in ref], [t for t, _ in hyp], autojunk=False
    )
    errors = []
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            r = ref[block.a + k][1]
            h = hyp[block.b + k][1]
            errors.append((abs(r.start - h.start) + abs(r.end - h.end)) / 2)
    if not errors:
        return None
    return BoundaryError(
        matched=len(errors),
        mean=sum(errors) / len(errors),
        p90=_percentile(errors, 0.9),
    )
