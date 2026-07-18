"""Unit tests for L13 conservative cross-source echo detection."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.live import (
    EchoDuplicateDetector,
    LiveOverlapTimeline,
    SegmentState,
    SegmentUpdate,
)


@dataclass
class _Segment:
    start: float
    end: float
    text: str
    speaker: str
    words: list = field(default_factory=list)


def _update(segment_id, start, end, text, speaker):
    return SegmentUpdate(
        segment_id,
        1,
        SegmentState.FINAL,
        _Segment(start=start, end=end, text=text, speaker=speaker),
    )


def test_exact_cross_source_overlap_is_deduplicated_and_marked_ambiguous():
    timeline = LiveOverlapTimeline(detector=EchoDuplicateDetector())
    assert timeline.accept(_update("mic:0:0", 1.0, 2.0, "Yes, okay!", "mic"))
    assert not timeline.accept(_update("system:0:0", 1.2, 2.2, " yes okay ", "system"))

    entries = timeline.entries()
    assert len(entries) == 1
    assert entries[0].ambiguous is True
    assert timeline.detector.duplicate_count == 1


def test_different_text_and_non_overlapping_repetition_are_preserved():
    detector = EchoDuplicateDetector()
    timeline = LiveOverlapTimeline(detector=detector)
    assert timeline.accept(_update("mic:0:0", 1.0, 2.0, "yes okay", "mic"))
    assert timeline.accept(_update("system:0:0", 1.2, 2.2, "yes please", "system"))
    assert timeline.accept(_update("system:1:0", 4.0, 5.0, "yes okay", "system"))

    assert len(timeline.final_segments()) == 3
    assert detector.duplicate_count == 0
