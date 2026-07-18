"""Unit tests for L10 source resampling and clock drift."""

from __future__ import annotations

from array import array
from dataclasses import replace

import pytest

from core.live import AudioFrame, DualSourceClockAligner, SourceClockAligner


def _frame(source, sequence, source_timestamp, monotonic_timestamp, rate=8_000, samples=800):
    pcm = array("h", [sequence] * samples).tobytes()
    return AudioFrame(
        source,
        sequence,
        source_timestamp,
        monotonic_timestamp,
        rate,
        pcm,
    )


def test_resamples_each_frame_to_target_rate_and_preserves_metadata():
    aligner = SourceClockAligner("system", target_sample_rate=16_000)
    frame = aligner.process(_frame("system", 0, 0.0, 10.0))

    assert frame.sample_rate == 16_000
    assert len(frame.pcm) == 3_200
    assert (frame.source, frame.sequence, frame.monotonic_timestamp) == (
        "system",
        0,
        10.0,
    )
    assert aligner.metrics().output_samples == 1_600


def test_two_sources_report_small_drift_and_correction():
    aligner = DualSourceClockAligner(drift_target_seconds=0.1)
    for sequence in range(600):  # 60 seconds at 100 ms per frame
        t = sequence * 0.1
        aligner.process(_frame("mic", sequence, t, t, rate=16_000, samples=1_600))
        aligner.process(
            _frame(
                "system",
                sequence,
                sequence * 0.10001,
                t,
                rate=8_000,
                samples=800,
            )
        )

    report = aligner.report()
    assert report.duration_seconds == pytest.approx(60.0, abs=0.02)
    assert report.within_target is True
    assert report.max_abs_drift_seconds < 0.1
    assert abs(report.per_source["system"].drift_ppm) < 200
    assert report.per_source["system"].correction_ppm > 0


def test_large_drift_fails_target_and_discontinuity_is_visible():
    aligner = DualSourceClockAligner(drift_target_seconds=0.1)
    for sequence in range(20):
        frame = _frame("mic", sequence, sequence * 0.11, sequence * 0.1, rate=16_000, samples=1_600)
        if sequence == 10:
            frame = AudioFrame(
                frame.source,
                frame.sequence,
                frame.source_timestamp,
                frame.monotonic_timestamp,
                frame.sample_rate,
                frame.pcm,
                discontinuity=True,
            )
        aligner.process(frame)

    report = aligner.report()
    assert report.within_target is False
    assert report.per_source["mic"].discontinuities == 1


def test_invalid_source_and_pcm_are_rejected():
    aligner = SourceClockAligner("mic")
    with pytest.raises(ValueError, match="for 'mic'"):
        aligner.process(_frame("system", 0, 0.0, 0.0))
    with pytest.raises(ValueError, match="int16"):
        aligner.process(AudioFrame("mic", 0, 0.0, 0.0, 16_000, b"odd"))


def test_device_timestamp_reset_starts_a_new_correction_epoch():
    aligner = SourceClockAligner("mic")
    for sequence in range(10):
        aligner.process(
            _frame("mic", sequence, sequence * 0.1, sequence * 0.1, rate=16_000, samples=1_600)
        )
    reset = _frame("mic", 10, 0.0, 1.0, rate=16_000, samples=1_600)
    reset = replace(reset, discontinuity=True)
    aligner.process(reset)

    metrics = aligner.metrics()
    assert metrics.correction_ppm == pytest.approx(0.0)
    assert metrics.discontinuities == 1
    assert abs(metrics.source_drift_seconds) < 0.01
