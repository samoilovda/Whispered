"""Cross-track bleed suppression for Zoom multitrack recordings.

A participant's own microphone track often also picks up their speakers, so
it can contain a quiet copy of whoever else is talking (acoustic echo /
"bleed"). Transcribing that copy would double every such utterance across
tracks. See docs/MULTITRACK_ZOOM_PLAN.ru.md (M3).

A segment is only treated as bleed — and dropped — when *all three* hold
against some segment on another track:
  1. time overlap covers at least ``overlap_ratio_threshold`` of the
     shorter of the two segments,
  2. normalized text matches exactly (reusing
     core.live.echo_detector.normalize_text, the same conservative
     normalization already validated for the Live pipeline), and
  3. this segment's energy is lower than the other's.

Matching on time alone would delete genuine interruptions/back-channel
speech, which is exactly the signal multitrack is meant to preserve — so
requiring an exact text match as well is deliberate, not a shortcut.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from core.live.echo_detector import normalize_text
from core.multitrack_audio import rms_in_range
from domain.transcription import Segment

DEFAULT_OVERLAP_RATIO_THRESHOLD = 0.5


@dataclass(frozen=True)
class TrackAudio:
    """One track's segments plus the decoded PCM they were transcribed
    from, needed to compare energy at a specific time range."""

    source: str
    segments: Tuple[Segment, ...]
    pcm: bytes
    sample_rate: int


@dataclass(frozen=True)
class BleedDecision:
    dropped_source: str
    dropped_text: str
    kept_source: str
    overlap_ratio: float


@dataclass(frozen=True)
class BleedResult:
    kept: Dict[str, List[Segment]]
    decisions: Tuple[BleedDecision, ...]
    bleed_suppressed_seconds: float


def _overlap_seconds(a: Segment, b: Segment) -> float:
    return min(a.end, b.end) - max(a.start, b.start)


def suppress_bleed(
    tracks: Sequence[TrackAudio],
    overlap_ratio_threshold: float = DEFAULT_OVERLAP_RATIO_THRESHOLD,
) -> BleedResult:
    """Drop segments judged to be acoustic bleed of another track's speech.

    Returns the surviving segments per source, plus one :class:`BleedDecision`
    per dropped segment for logging/metrics
    (``bleed_suppressed_seconds`` in the benchmark report).
    """
    dropped_ids: set = set()
    decisions: List[BleedDecision] = []

    indexed = [
        (track, [(i, seg) for i, seg in enumerate(track.segments)]) for track in tracks
    ]

    for a_idx in range(len(indexed)):
        track_a, segs_a = indexed[a_idx]
        for b_idx in range(a_idx + 1, len(indexed)):
            track_b, segs_b = indexed[b_idx]
            for i_a, seg_a in segs_a:
                key_a = (track_a.source, i_a)
                if key_a in dropped_ids:
                    continue
                for i_b, seg_b in segs_b:
                    key_b = (track_b.source, i_b)
                    if key_b in dropped_ids:
                        continue
                    overlap = _overlap_seconds(seg_a, seg_b)
                    if overlap <= 0:
                        continue
                    shorter = min(seg_a.end - seg_a.start, seg_b.end - seg_b.start)
                    if shorter <= 0:
                        continue
                    ratio = overlap / shorter
                    if ratio < overlap_ratio_threshold:
                        continue
                    if normalize_text(seg_a.text) != normalize_text(seg_b.text):
                        continue
                    if not normalize_text(seg_a.text):
                        continue

                    energy_a = rms_in_range(track_a.pcm, track_a.sample_rate, seg_a.start, seg_a.end)
                    energy_b = rms_in_range(track_b.pcm, track_b.sample_rate, seg_b.start, seg_b.end)
                    if energy_a == energy_b:
                        continue  # ambiguous tie: keep both rather than guess

                    if energy_a < energy_b:
                        dropped_ids.add(key_a)
                        decisions.append(BleedDecision(
                            dropped_source=track_a.source, dropped_text=seg_a.text,
                            kept_source=track_b.source, overlap_ratio=ratio,
                        ))
                        break  # seg_a is gone; stop matching it against more of track_b
                    else:
                        dropped_ids.add(key_b)
                        decisions.append(BleedDecision(
                            dropped_source=track_b.source, dropped_text=seg_b.text,
                            kept_source=track_a.source, overlap_ratio=ratio,
                        ))

    kept: Dict[str, List[Segment]] = {}
    suppressed_seconds = 0.0
    for track, segs in indexed:
        kept[track.source] = [seg for i, seg in segs if (track.source, i) not in dropped_ids]
    for track, segs in indexed:
        for i, seg in segs:
            if (track.source, i) in dropped_ids:
                suppressed_seconds += seg.end - seg.start

    return BleedResult(kept=kept, decisions=tuple(decisions), bleed_suppressed_seconds=suppressed_seconds)
