"""Stable, UI-independent contracts for the live transcription pipeline.

The batch data model remains unchanged. ``SegmentUpdate`` is a separate
envelope, so consumers that only understand final ``Segment`` objects never
receive partial text or revision bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """Timestamped PCM emitted by an audio callback."""

    source: str
    sequence: int
    source_timestamp: float
    monotonic_timestamp: float
    sample_rate: int
    pcm: bytes
    discontinuity: bool = False

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("AudioFrame.source must not be empty")
        if self.sequence < 0:
            raise ValueError("AudioFrame.sequence must be non-negative")
        if self.sample_rate <= 0:
            raise ValueError("AudioFrame.sample_rate must be positive")
        if self.monotonic_timestamp < 0:
            raise ValueError("AudioFrame.monotonic_timestamp must be non-negative")


@dataclass(frozen=True, slots=True)
class SpeechTurn:
    """A VAD-bounded run of speech for exactly one audio source."""

    source: str
    start: float
    end: float
    first_sequence: int
    last_sequence: int
    discontinuity: bool = False

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("SpeechTurn.source must not be empty")
        if self.start < 0 or self.end < self.start:
            raise ValueError("SpeechTurn timestamps must be ordered and non-negative")
        if self.first_sequence < 0 or self.last_sequence < self.first_sequence:
            raise ValueError("SpeechTurn sequence range is invalid")


class SegmentState(str, Enum):
    """Visibility state of a live transcript segment."""

    PARTIAL = "partial"
    FINAL = "final"


@dataclass(frozen=True, slots=True)
class SegmentUpdate:
    """A revisioned live envelope around a regular final-model segment.

    ``segment`` deliberately avoids importing the Qt-facing batch transcriber
    in audio callbacks. Producers must supply a ``transcriber.Segment``.
    """

    segment_id: str
    revision: int
    state: SegmentState
    segment: Any

    def __post_init__(self) -> None:
        if not self.segment_id:
            raise ValueError("SegmentUpdate.segment_id must not be empty")
        if self.revision < 1:
            raise ValueError("SegmentUpdate.revision must be positive")
        for attribute in ("start", "end", "text", "speaker", "words"):
            if not hasattr(self.segment, attribute):
                raise TypeError(f"SegmentUpdate.segment lacks {attribute!r}")


class LiveSegmentRevisions:
    """State machine that guarantees final segments cannot be revised."""

    def __init__(self) -> None:
        self._updates: dict[str, SegmentUpdate] = {}

    def begin(self, segment_id: str, segment: Any) -> SegmentUpdate:
        """Publish the first mutable (partial) revision."""
        if segment_id in self._updates:
            raise ValueError(f"Segment {segment_id!r} already exists")
        return self._store(segment_id, SegmentState.PARTIAL, segment, revision=1)

    def revise(self, segment_id: str, segment: Any) -> SegmentUpdate:
        """Publish the next partial revision of an active segment."""
        previous = self._active(segment_id)
        return self._store(segment_id, SegmentState.PARTIAL, segment, previous.revision + 1)

    def finalize(self, segment_id: str, segment: Any) -> SegmentUpdate:
        """Publish the terminal revision; later revisions are rejected."""
        previous = self._active(segment_id)
        return self._store(segment_id, SegmentState.FINAL, segment, previous.revision + 1)

    def latest(self, segment_id: str) -> SegmentUpdate | None:
        """Return the most recently published revision, if any."""
        return self._updates.get(segment_id)

    def _active(self, segment_id: str) -> SegmentUpdate:
        try:
            previous = self._updates[segment_id]
        except KeyError as exc:
            raise KeyError(f"Unknown segment {segment_id!r}") from exc
        if previous.state is SegmentState.FINAL:
            raise ValueError(f"Segment {segment_id!r} is already final")
        return previous

    def _store(
        self,
        segment_id: str,
        state: SegmentState,
        segment: Any,
        revision: int,
    ) -> SegmentUpdate:
        update = SegmentUpdate(segment_id, revision, state, segment)
        self._updates[segment_id] = update
        return update
