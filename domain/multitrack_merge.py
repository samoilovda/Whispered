"""Merge per-track transcripts of a Zoom multitrack recording into one
``TranscriptionResult`` — see docs/MULTITRACK_ZOOM_PLAN.ru.md (M4).

Because participant identity comes from which file a segment was
transcribed from (not from diarization), each track gets a stable speaker
id (``track_1``, ``track_2``, ...) and its Zoom display name goes into
``TranscriptionResult.speaker_names`` — the same field the UI already uses
for user-renamed diarization speakers, so renaming and every existing
exporter (``exporters.py``) work unchanged.

Qt-free like the rest of ``domain/`` — enforced by
``tests/test_multitrack_merge.py`` alongside
``tests/test_domain_transcription.py``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Sequence, Tuple

from domain.transcription import Segment, TranscriptionResult


@dataclass(frozen=True)
class TrackResult:
    """One track's transcript, ready to be folded into the merged result."""

    source: str          # stable id, e.g. "track_1"
    display_name: str    # Zoom-derived name, e.g. "Den"
    segments: Tuple[Segment, ...]
    language: str


def merge_track_results(
    track_results: Sequence[TrackResult],
    total_duration: float,
) -> TranscriptionResult:
    """Combine every track's segments into one chronologically sorted
    ``TranscriptionResult``.

    ``total_duration`` is the recording's actual length (from the mixed
    audio or the longest track's probed duration) rather than the last
    segment's end time, since trailing silence carries no segment.
    Overlapping segments from different tracks are kept as-is — they
    represent real simultaneous speech and are not merged or shifted.
    """
    if not track_results:
        raise ValueError("merge_track_results requires at least one track")

    all_segments = []
    for track in track_results:
        for seg in track.segments:
            all_segments.append(replace(seg, speaker=track.source))
    all_segments.sort(key=lambda s: s.start)

    language_counts = Counter(t.language for t in track_results)
    language = language_counts.most_common(1)[0][0]

    speaker_names = {t.source: t.display_name for t in track_results}

    return TranscriptionResult(
        segments=all_segments,
        language=language,
        duration=total_duration,
        speaker_names=speaker_names,
    )
