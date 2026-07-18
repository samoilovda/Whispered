"""Unit tests for L7 live segment reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from core.live import (
    LiveSegmentReconciler,
    SegmentState,
    SpeechTurn,
    StablePrefixTracker,
)


@dataclass
class _Segment:
    start: float
    end: float
    text: str
    speaker: str | None = "mic"
    words: list = field(default_factory=list)


def _turn(first=0, start=0.0, end=3.0):
    return SpeechTurn("mic", start, end, first, first + 9)


def test_stable_prefix_grows_only_from_consecutive_exact_words():
    tracker = StablePrefixTracker()
    assert tracker.observe("mic:0:0", "hello wor") == ""
    assert tracker.observe("mic:0:0", "hello world") == "hello"
    assert tracker.observe("mic:0:0", "hello world from here") == "hello world"
    assert tracker.get("mic:0:0") == "hello world"


def test_partial_tail_keeps_segment_id_and_revision_then_final_is_terminal():
    reconciler = LiveSegmentReconciler("mic")
    turn = _turn()

    first = reconciler.accept(
        turn, [_Segment(0.0, 1.0, "hello wor")], final=False
    )
    second = reconciler.accept(
        turn, [_Segment(0.0, 1.1, "hello world")], final=False
    )
    final = reconciler.accept(
        turn, [_Segment(0.0, 1.2, "hello world")], final=True
    )

    assert first[0].segment_id == second[0].segment_id == final[0].segment_id
    assert [update.revision for update in (first[0], second[0], final[0])] == [1, 2, 3]
    assert final[0].state is SegmentState.FINAL
    assert reconciler.stable_prefix(final[0].segment_id) == "hello world"
    assert reconciler.accept(turn, [_Segment(0.0, 1.3, "changed")], final=False) == ()


def test_partial_decode_cannot_rewrite_confirmed_prefix():
    reconciler = LiveSegmentReconciler("mic")
    turn = _turn()
    reconciler.accept(turn, [_Segment(0.0, 1.0, "hello world")], final=False)
    reconciler.accept(turn, [_Segment(0.0, 1.0, "hello world again")], final=False)
    update = reconciler.accept(turn, [_Segment(0.0, 1.0, "changed entirely")], final=False)[0]

    assert update.segment.text == "hello world"


def test_final_without_visible_partial_is_revision_one():
    reconciler = LiveSegmentReconciler("mic")
    update = reconciler.accept(
        _turn(), [_Segment(0.0, 1.0, "final immediately")], final=True
    )[0]
    assert (update.revision, update.state) == (1, SegmentState.FINAL)


def test_overlapping_exact_text_is_deduplicated_but_separate_repetition_survives():
    reconciler = LiveSegmentReconciler("mic")
    first = _Segment(1.0, 2.0, "Yes, okay!")
    assert len(reconciler.accept(_turn(first=0, start=1.0, end=2.0), [first], final=True)) == 1

    duplicate = _Segment(1.8, 2.8, " yes okay ")
    assert reconciler.accept(_turn(first=10, start=1.8, end=2.8), [duplicate], final=True) == ()

    intentional = _Segment(3.0, 4.0, "Yes, okay!")
    assert len(reconciler.accept(_turn(first=20, start=3.0, end=4.0), [intentional], final=True)) == 1
    assert reconciler.stats().duplicate_segments == 1


def test_different_source_cannot_enter_one_source_reconciler():
    reconciler = LiveSegmentReconciler("mic")
    with pytest.raises(ValueError, match="for 'mic'"):
        reconciler.accept(SpeechTurn("system", 0, 1, 0, 1), [], final=True)
