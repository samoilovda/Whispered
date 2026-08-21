"""core/multitrack_bleed.py: the 4 cases required by
docs/MULTITRACK_ZOOM_PLAN.ru.md M3 DoD, plus tie/threshold edges."""

from __future__ import annotations

import struct

from core.multitrack_bleed import TrackAudio, suppress_bleed
from domain.transcription import Segment

SAMPLE_RATE = 16_000


def _pcm(amplitude: int, duration_s: float = 5.0) -> bytes:
    n = int(SAMPLE_RATE * duration_s)
    return struct.pack(f"<{n}h", *([amplitude] * n))


def test_genuine_interruption_different_text_both_kept():
    a = TrackAudio(
        source="den",
        segments=(Segment(start=0.0, end=2.0, text="и вот смотри как это работает"),),
        pcm=_pcm(8000), sample_rate=SAMPLE_RATE,
    )
    b = TrackAudio(
        source="roman",
        segments=(Segment(start=1.5, end=2.5, text="подожди секунду"),),
        pcm=_pcm(6000), sample_rate=SAMPLE_RATE,
    )
    result = suppress_bleed([a, b])
    assert len(result.kept["den"]) == 1
    assert len(result.kept["roman"]) == 1
    assert result.decisions == ()


def test_explicit_bleed_identical_text_quieter_copy_dropped():
    a = TrackAudio(
        source="den",
        segments=(Segment(start=0.0, end=2.0, text="это очень интересная тема"),),
        pcm=_pcm(9000), sample_rate=SAMPLE_RATE,
    )
    # roman's mic picked up den's speech through his speakers: same text,
    # much quieter, near-identical timing.
    b = TrackAudio(
        source="roman",
        segments=(Segment(start=0.05, end=1.95, text="это очень интересная тема"),),
        pcm=_pcm(500), sample_rate=SAMPLE_RATE,
    )
    result = suppress_bleed([a, b])
    assert len(result.kept["den"]) == 1
    assert result.kept["roman"] == []
    assert len(result.decisions) == 1
    assert result.decisions[0].dropped_source == "roman"
    assert result.decisions[0].kept_source == "den"
    assert result.bleed_suppressed_seconds > 0


def test_identical_text_no_time_overlap_both_kept():
    a = TrackAudio(
        source="den",
        segments=(Segment(start=0.0, end=1.0, text="хорошо"),),
        pcm=_pcm(9000), sample_rate=SAMPLE_RATE,
    )
    b = TrackAudio(
        source="roman",
        segments=(Segment(start=10.0, end=11.0, text="хорошо"),),
        pcm=_pcm(500), sample_rate=SAMPLE_RATE,
    )
    result = suppress_bleed([a, b])
    assert len(result.kept["den"]) == 1
    assert len(result.kept["roman"]) == 1
    assert result.decisions == ()


def test_overlap_but_different_text_both_kept():
    a = TrackAudio(
        source="den",
        segments=(Segment(start=0.0, end=2.0, text="первый вариант"),),
        pcm=_pcm(9000), sample_rate=SAMPLE_RATE,
    )
    b = TrackAudio(
        source="roman",
        segments=(Segment(start=0.5, end=1.5, text="второй вариант"),),
        pcm=_pcm(500), sample_rate=SAMPLE_RATE,
    )
    result = suppress_bleed([a, b])
    assert len(result.kept["den"]) == 1
    assert len(result.kept["roman"]) == 1
    assert result.decisions == ()


def test_energy_tie_keeps_both_rather_than_guessing():
    a = TrackAudio(
        source="den",
        segments=(Segment(start=0.0, end=2.0, text="равная громкость"),),
        pcm=_pcm(4000), sample_rate=SAMPLE_RATE,
    )
    b = TrackAudio(
        source="roman",
        segments=(Segment(start=0.0, end=2.0, text="равная громкость"),),
        pcm=_pcm(4000), sample_rate=SAMPLE_RATE,
    )
    result = suppress_bleed([a, b])
    assert len(result.kept["den"]) == 1
    assert len(result.kept["roman"]) == 1
    assert result.decisions == ()


def test_overlap_below_threshold_both_kept():
    # seg_a (the shorter one, duration 1.0) only overlaps seg_b by 0.3s —
    # 30% of its own length, below the 50% ratio threshold.
    a = TrackAudio(
        source="den",
        segments=(Segment(start=0.0, end=1.0, text="одно и то же"),),
        pcm=_pcm(9000), sample_rate=SAMPLE_RATE,
    )
    b = TrackAudio(
        source="roman",
        segments=(Segment(start=0.7, end=2.0, text="одно и то же"),),
        pcm=_pcm(500), sample_rate=SAMPLE_RATE,
    )
    result = suppress_bleed([a, b], overlap_ratio_threshold=0.5)
    assert len(result.kept["den"]) == 1
    assert len(result.kept["roman"]) == 1
    assert result.decisions == ()
