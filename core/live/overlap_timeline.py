"""Source labels and an overlap-preserving live segment timeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.live.contracts import SegmentState, SegmentUpdate


DEFAULT_SOURCE_LABELS = {
    "mic": "Microphone",
    "system": "Meeting audio",
}


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """Current revision plus presentation metadata for one segment."""

    update: SegmentUpdate
    source: str
    label: str
    overlap_ids: tuple[str, ...]
    ambiguous: bool = False


class SourceSpeakerLabels:
    """Stable source IDs with user-editable display labels."""

    def __init__(self, labels: dict[str, str] | None = None) -> None:
        self._labels = dict(DEFAULT_SOURCE_LABELS)
        if labels:
            self._labels.update(labels)

    def label(self, source: str) -> str:
        return self._labels.get(source, source.replace("_", " ").title())

    def rename(self, source: str, label: str) -> None:
        if not source:
            raise ValueError("source must not be empty")
        clean = label.strip()
        if not clean:
            raise ValueError("label must not be empty")
        self._labels[source] = clean

    def as_dict(self) -> dict[str, str]:
        return dict(self._labels)


class LiveOverlapTimeline:
    """Keep partial/final revisions sorted without flattening overlaps."""

    def __init__(self, labels: SourceSpeakerLabels | None = None, detector: Any = None) -> None:
        self.labels = labels or SourceSpeakerLabels()
        self.detector = detector
        self._updates: dict[str, SegmentUpdate] = {}
        self._sources: dict[str, str] = {}
        self._ambiguous: set[str] = set()

    def accept(self, update: SegmentUpdate) -> bool:
        """Store the newest revision; return false when L13 filters an echo."""
        source = _source_for(update)
        if update.state is SegmentState.FINAL and self.detector is not None:
            decision = self.detector.register(update.segment_id, source, update.segment)
            if decision.duplicate:
                self._updates.pop(update.segment_id, None)
                self._sources.pop(update.segment_id, None)
                if decision.canonical_id:
                    self._ambiguous.add(decision.canonical_id)
                return False
        self._updates[update.segment_id] = update
        self._sources[update.segment_id] = source
        return True

    def entries(self) -> tuple[TimelineEntry, ...]:
        updates = sorted(
            self._updates.values(),
            key=lambda item: (
                float(item.segment.start),
                float(item.segment.end),
                item.segment_id,
            ),
        )
        entries = []
        for update in updates:
            source = self._sources[update.segment_id]
            overlaps = tuple(
                other.segment_id
                for other in updates
                if other.segment_id != update.segment_id
                and self._sources[other.segment_id] != source
                and _overlap(update.segment, other.segment) > 0
            )
            entries.append(
                TimelineEntry(
                    update=update,
                    source=source,
                    label=self.labels.label(source),
                    overlap_ids=overlaps,
                    ambiguous=update.segment_id in self._ambiguous,
                )
            )
        return tuple(entries)

    def final_segments(self) -> tuple[Any, ...]:
        return tuple(
            entry.update.segment
            for entry in self.entries()
            if entry.update.state is SegmentState.FINAL
        )

    def clear(self) -> None:
        self._updates.clear()
        self._sources.clear()
        self._ambiguous.clear()


def _source_for(update: SegmentUpdate) -> str:
    speaker = getattr(update.segment, "speaker", None)
    if speaker:
        return str(speaker)
    return update.segment_id.split(":", 1)[0]


def _overlap(first: Any, second: Any) -> float:
    return min(float(first.end), float(second.end)) - max(
        float(first.start), float(second.start)
    )
