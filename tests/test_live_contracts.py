"""Unit tests for L2 live contracts; no microphone, model, or Qt required."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from core.live.contracts import AudioFrame, LiveSegmentRevisions, SegmentState, SpeechTurn


@dataclass
class _Segment:
    start: float = 0.0
    end: float = 1.0
    text: str = "draft"
    speaker: str | None = "Microphone"
    words: list = field(default_factory=list)


def test_audio_frame_validates_callback_metadata():
    frame = AudioFrame("mic", 0, 1.0, 1.1, 16_000, b"pcm")
    assert frame.sequence == 0
    assert frame.discontinuity is False

    with pytest.raises(ValueError, match="sample_rate"):
        AudioFrame("mic", 0, 1.0, 1.1, 0, b"")


def test_speech_turn_keeps_one_source_and_a_valid_frame_range():
    turn = SpeechTurn("mic", 1.0, 2.5, 4, 9)
    assert turn.source == "mic"

    with pytest.raises(ValueError, match="sequence range"):
        SpeechTurn("mic", 1.0, 2.5, 9, 4)


def test_partial_revisions_become_immutable_after_final():
    revisions = LiveSegmentRevisions()
    first = revisions.begin("mic:100", _Segment(text="hel"))
    second = revisions.revise("mic:100", _Segment(text="hello"))
    final = revisions.finalize("mic:100", _Segment(text="hello world"))

    assert (first.revision, first.state) == (1, SegmentState.PARTIAL)
    assert (second.revision, second.state) == (2, SegmentState.PARTIAL)
    assert (final.revision, final.state, final.segment.text) == (3, SegmentState.FINAL, "hello world")
    assert revisions.latest("mic:100") == final

    with pytest.raises(ValueError, match="already final"):
        revisions.revise("mic:100", _Segment(text="must not appear"))


def test_unknown_segment_cannot_be_finalized():
    with pytest.raises(KeyError, match="Unknown segment"):
        LiveSegmentRevisions().finalize("missing", _Segment())
