"""Speaker diarization results and their merge into a transcript.

Qt-free and engine-free: ``diarizer.py`` produces a ``DiarizationResult``;
the transcription child hands it to ``assign_speakers`` to label the
transcript's segments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from domain.transcription import Segment


@dataclass
class SpeakerSegment:
    """A stretch of audio attributed to one speaker."""
    start: float      # seconds
    end: float        # seconds
    speaker: str      # "Speaker 1", "Speaker 2", … in order of first appearance
    confidence: float = 1.0


@dataclass
class DiarizationResult:
    """Who speaks when, over a whole recording."""
    segments: List[SpeakerSegment]
    num_speakers: int
    duration: float

    def get_speaker_at(self, time: float) -> Optional[str]:
        """The speaker talking at *time*, or None in silence."""
        for seg in self.segments:
            if seg.start <= time <= seg.end:
                return seg.speaker
        return None

    def speaker_at_span(self, start: float, end: float) -> Optional[str]:
        """The speaker who talks the most within [start, end]; for a span
        that touches no speech, the one whose speech is nearest to it."""
        talk: Dict[str, float] = {}
        for seg in self.segments:
            overlap = min(end, seg.end) - max(start, seg.start)
            if overlap > 0:
                talk[seg.speaker] = talk.get(seg.speaker, 0.0) + overlap
        if talk:
            return max(talk, key=lambda s: talk[s])
        nearest: Optional[str] = None
        best = float("inf")
        for seg in self.segments:
            gap = max(seg.start - end, start - seg.end)
            if gap < best:
                best, nearest = gap, seg.speaker
        return nearest


def assign_speakers(segments: Sequence[Segment], diarization: DiarizationResult) -> int:
    """Set ``speaker`` on every transcript segment in place from
    *diarization*: the speaker who talks the most during the segment
    (a sentence that straddles a turn change goes to whoever said most of
    it, not to whoever happened to talk at its midpoint). Returns how many
    segments got a speaker."""
    labelled = 0
    for seg in segments:
        seg.speaker = diarization.speaker_at_span(seg.start, seg.end)
        if seg.speaker is not None:
            labelled += 1
    return labelled
