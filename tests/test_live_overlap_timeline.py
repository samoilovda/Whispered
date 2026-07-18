"""Unit tests for L12 source-labelled overlap timeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.live import (
    LiveOverlapTimeline,
    SegmentState,
    SegmentUpdate,
    SourceSpeakerLabels,
)


@dataclass
class _Segment:
    start: float
    end: float
    text: str
    speaker: str
    words: list = field(default_factory=list)


def _update(segment_id, start, end, text, speaker, revision=1, state=SegmentState.FINAL):
    return SegmentUpdate(
        segment_id,
        revision,
        state,
        _Segment(start=start, end=end, text=text, speaker=speaker),
    )


def test_timeline_keeps_two_sources_sorted_and_marks_overlap():
    timeline = LiveOverlapTimeline()
    assert timeline.accept(_update("system:0:0", 1.0, 3.0, "remote", "system"))
    assert timeline.accept(_update("mic:0:0", 1.5, 2.5, "local", "mic"))

    entries = timeline.entries()
    assert [entry.source for entry in entries] == ["system", "mic"]
    assert entries[0].label == "Meeting audio"
    assert entries[1].label == "Microphone"
    assert entries[0].overlap_ids == ("mic:0:0",)
    assert entries[1].overlap_ids == ("system:0:0",)
    assert len(timeline.final_segments()) == 2


def test_partial_revision_replaces_only_same_segment_id():
    timeline = LiveOverlapTimeline()
    timeline.accept(_update("mic:2:0", 0.0, 1.0, "hello", "mic", state=SegmentState.PARTIAL))
    timeline.accept(
        _update("mic:2:0", 0.0, 1.2, "hello world", "mic", revision=2, state=SegmentState.PARTIAL)
    )

    entries = timeline.entries()
    assert len(entries) == 1
    assert entries[0].update.revision == 2
    assert entries[0].update.segment.text == "hello world"


def test_source_labels_can_be_renamed_without_changing_source_ids():
    labels = SourceSpeakerLabels()
    labels.rename("system", "Удалённый звук")
    timeline = LiveOverlapTimeline(labels)
    timeline.accept(_update("system:0:0", 0.0, 1.0, "hello", "system"))

    assert timeline.entries()[0].source == "system"
    assert timeline.entries()[0].label == "Удалённый звук"
