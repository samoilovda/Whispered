"""core/asr_metrics.py: WER/CER, silence hallucinations, repeats, boundaries."""

from __future__ import annotations

import pytest

from core import asr_metrics as m
from core.asr_metrics import TimedSpan, TimedWord


def test_normalize_folds_case_punctuation_and_yo():
    assert m.normalize_words("Всё, ЧТО  было — Ёлка!") == ["все", "что", "было", "елка"]


def test_wer_known_pairs():
    assert m.wer("a b c d", "a b c d") == 0
    assert m.wer("a b c d", "a x c") == pytest.approx(0.5)  # 1 sub + 1 del
    assert m.wer("", "") == 0
    assert m.wer("", "a b") == 2
    assert m.wer("Привет, мир", "привет мир") == 0


def test_cer_known_pair():
    assert m.cer("abcd", "abxd") == pytest.approx(0.25)


def test_silence_intervals():
    words = [TimedWord(0, 1, "a"), TimedWord(4, 5, "b"), TimedWord(5.5, 6, "c")]
    assert m.silence_intervals(words, 10) == [(1, 4), (6, 10)]
    assert m.silence_intervals(words, 7) == [(1, 4)]
    # an overlapping long word hides the "gap" behind it
    long = [TimedWord(0, 6, "a"), TimedWord(1, 2, "b"), TimedWord(8, 9, "c")]
    assert m.silence_intervals(long, 9) == [(6, 8)]


def test_silence_hallucination_counts_words_in_gap():
    spans = [
        TimedSpan(0, 1, "hello"),
        TimedSpan(2, 4, "thanks for watching"),   # inside (1, 4)
    ]
    assert m.silence_hallucination_words(spans, [(1.5, 5)]) == 3
    assert m.silence_hallucination_words(spans, []) == 0


def test_repeats():
    assert m.repeated_words("да да да".split()) == 0           # live speech
    assert m.repeated_words("да да да да да".split()) == 4
    assert m.repeated_words("a b a b a b x".split()) == 4
    assert m.repeated_words("one two three".split()) == 0


def test_repeat_count_counts_identical_segments():
    spans = [TimedSpan(i, i + 1, "Продолжение следует") for i in range(4)]
    # 8 words = 4 reps of a 2-word group (6 excess) is counted by the word
    # scan; the segment run adds its own excess
    assert m.repeat_count(spans) == 6 + 3
    assert m.repeat_count([TimedSpan(0, 1, "a"), TimedSpan(1, 2, "b")]) == 0


def test_boundary_error_pairs_matching_words():
    ref = [TimedWord(0, 1, "один"), TimedWord(1, 2, "два"), TimedWord(2, 3, "три")]
    hyp = [TimedWord(0.1, 1.1, "Один"), TimedWord(1.5, 2.5, "ошибка"),
           TimedWord(2.4, 3.4, "три")]
    err = m.boundary_error(ref, hyp)
    assert err is not None
    assert err.matched == 2
    assert err.mean == pytest.approx((0.1 + 0.4) / 2)
    assert m.boundary_error(ref, [TimedWord(0, 1, "x")]) is None
