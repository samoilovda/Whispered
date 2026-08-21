"""core/multitrack_audio.py speech-window extraction, on synthetic PCM
(silence/tone/silence) per docs/MULTITRACK_ZOOM_PLAN.ru.md M2 DoD."""

from __future__ import annotations

import math
import struct

import pytest

from core.multitrack_audio import (
    SpeechWindow,
    build_gated_track,
    detect_speech_windows,
    read_wav_int16_mono,
    remap_gated_time,
    remap_segment_to_track_time,
    rms_in_range,
    speech_coverage_seconds,
    wav_duration_seconds,
    wav_to_frames,
    write_wav,
)
from domain.transcription import Segment

SAMPLE_RATE = 16_000


def _tone(duration_s: float, freq: float = 440.0, amplitude: int = 12000) -> bytes:
    n = int(SAMPLE_RATE * duration_s)
    samples = [int(amplitude * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)) for i in range(n)]
    return struct.pack(f"<{n}h", *samples)


def _silence(duration_s: float) -> bytes:
    n = int(SAMPLE_RATE * duration_s)
    return b"\x00\x00" * n


def _write_pattern(path: str, *chunks: bytes) -> None:
    write_wav(path, b"".join(chunks), sample_rate=SAMPLE_RATE)


def test_wav_round_trip(tmp_path):
    path = str(tmp_path / "x.wav")
    pcm = _tone(0.5)
    write_wav(path, pcm)
    read_pcm, sr = read_wav_int16_mono(path)
    assert sr == SAMPLE_RATE
    assert read_pcm == pcm


def test_wav_to_frames_covers_whole_signal_in_order(tmp_path):
    path = str(tmp_path / "x.wav")
    _write_pattern(path, _tone(1.0))
    frames = list(wav_to_frames(path, source="t"))
    assert frames
    assert all(f.source == "t" for f in frames)
    timestamps = [f.monotonic_timestamp for f in frames]
    assert timestamps == sorted(timestamps)
    total_pcm = b"".join(f.pcm for f in frames)
    assert len(total_pcm) <= SAMPLE_RATE * 2  # int16 bytes for 1s, allowing tail truncation


def test_detect_speech_windows_silence_tone_silence(tmp_path):
    path = str(tmp_path / "sts.wav")
    _write_pattern(path, _silence(2.0), _tone(3.0), _silence(2.0))
    windows = detect_speech_windows(
        path,
        source="participant",
        context_padding_seconds=0.2,
        end_silence_seconds=0.5,
        min_duration_seconds=0.4,
    )
    assert len(windows) == 1
    w = windows[0]
    # Tone starts at 2.0s and runs 3.0s; padding adds ~0.2s each side.
    assert 1.7 <= w.start <= 2.3
    assert 4.6 <= w.end <= 5.3
    assert w.pcm


def test_detect_speech_windows_pure_silence_yields_nothing(tmp_path):
    path = str(tmp_path / "silence.wav")
    _write_pattern(path, _silence(2.0))
    windows = detect_speech_windows(path, source="p")
    assert windows == []


def test_detect_speech_windows_merges_close_speech_runs(tmp_path):
    path = str(tmp_path / "merge.wav")
    # Two tone bursts separated by a gap shorter than end_silence_seconds
    # should merge into a single window.
    _write_pattern(path, _silence(1.0), _tone(1.0), _silence(0.3), _tone(1.0), _silence(1.0))
    windows = detect_speech_windows(
        path, source="p", end_silence_seconds=0.6, context_padding_seconds=0.1, min_duration_seconds=0.1
    )
    assert len(windows) == 1


def test_detect_speech_windows_drops_short_bursts_below_min_duration(tmp_path):
    path = str(tmp_path / "short.wav")
    _write_pattern(path, _silence(1.0), _tone(0.05), _silence(2.0))
    windows = detect_speech_windows(
        path, source="p", end_silence_seconds=0.3, context_padding_seconds=0.01, min_duration_seconds=0.4
    )
    assert windows == []


def test_rms_in_range_silence_is_near_zero_tone_is_higher():
    silence_pcm = _silence(1.0)
    tone_pcm = _tone(1.0)
    assert rms_in_range(silence_pcm, SAMPLE_RATE, 0.0, 1.0) == pytest.approx(0.0, abs=1e-6)
    assert rms_in_range(tone_pcm, SAMPLE_RATE, 0.0, 1.0) > 0.1


def test_rms_in_range_empty_range_is_zero():
    tone_pcm = _tone(1.0)
    assert rms_in_range(tone_pcm, SAMPLE_RATE, 0.5, 0.5) == 0.0


def test_speech_coverage_seconds_sums_window_durations():
    windows = [SpeechWindow(0.0, 1.5, b""), SpeechWindow(3.0, 4.0, b"")]
    assert speech_coverage_seconds(windows) == pytest.approx(2.5)


def test_wav_duration_seconds(tmp_path):
    path = str(tmp_path / "dur.wav")
    write_wav(path, _tone(2.0))
    assert wav_duration_seconds(path) == pytest.approx(2.0, abs=0.01)


def test_build_gated_track_concatenates_with_gaps():
    windows = [
        SpeechWindow(start=10.0, end=11.0, pcm=_tone(1.0)),
        SpeechWindow(start=20.0, end=21.5, pcm=_tone(1.5)),
    ]
    gated = build_gated_track(windows, sample_rate=SAMPLE_RATE, gap_seconds=0.5)
    assert gated.mapping == (
        (0.0, 1.0, 10.0),
        (1.5, 3.0, 20.0),
    )
    expected_len = len(windows[0].pcm) + len(windows[1].pcm) + int(SAMPLE_RATE * 0.5) * 2
    assert len(gated.pcm) == expected_len


def test_remap_gated_time_inside_a_window():
    mapping = ((0.0, 1.0, 10.0), (1.5, 3.0, 20.0))
    assert remap_gated_time(0.4, mapping) == pytest.approx(10.4)
    assert remap_gated_time(2.0, mapping) == pytest.approx(20.5)


def test_remap_gated_time_before_first_window_clamps_to_start():
    mapping = ((1.0, 2.0, 10.0), (3.0, 4.0, 20.0))
    assert remap_gated_time(0.0, mapping) == pytest.approx(10.0)


def test_remap_gated_time_in_gap_clamps_to_preceding_window_end():
    mapping = ((0.0, 1.0, 10.0), (1.5, 3.0, 20.0))
    assert remap_gated_time(1.2, mapping) == pytest.approx(11.0)


def test_remap_gated_time_empty_mapping_is_identity():
    assert remap_gated_time(5.0, ()) == 5.0


def test_remap_segment_to_track_time():
    mapping = ((0.0, 1.0, 10.0), (1.5, 3.0, 20.0))
    seg = Segment(start=0.2, end=0.8, text="hi", speaker="track_1")
    remapped = remap_segment_to_track_time(seg, mapping)
    assert remapped.start == pytest.approx(10.2)
    assert remapped.end == pytest.approx(10.8)
    assert remapped.text == "hi"
    assert remapped.speaker == "track_1"


def test_remap_segment_spanning_two_windows_stays_anchored_to_one_window():
    # windows.py bug regression: window 0 is real time [1049.5, 1050.0],
    # window 1 is real time [1200.0, 1205.0] — minutes apart in the real
    # track even though they're only 0.3s apart in the gated timeline.
    # A whisper segment that (wrongly) spans the gated boundary between
    # them must NOT get start and end pulled from two different windows —
    # that would fabricate a multi-minute-long segment and break
    # chronological ordering once merged with other tracks.
    mapping = ((0.0, 0.5, 1049.5), (0.8, 5.8, 1200.0))
    # start (0.6) is past window 0's gated end (0.5), in the gap; end (1.0)
    # is inside window 1. Midpoint (0.8) falls in window 1 -> both
    # timestamps anchored there, duration preserved exactly, no
    # cross-window jump of ~150 real seconds.
    seg = Segment(start=0.6, end=1.0, text="spans the gap", speaker="track_1")
    remapped = remap_segment_to_track_time(seg, mapping)
    assert remapped.end - remapped.start == pytest.approx(seg.end - seg.start)
    assert remapped.start == pytest.approx(1199.8)
    assert remapped.end == pytest.approx(1200.2)


def test_remap_segment_empty_mapping_returns_unchanged():
    seg = Segment(start=1.0, end=2.0, text="x")
    assert remap_segment_to_track_time(seg, ()) is seg


def test_probe_media_duration_on_synthetic_wav(tmp_path):
    from core.external_tools import resolve_tool
    from core.multitrack_audio import probe_media_duration

    if not resolve_tool("ffprobe"):
        pytest.skip("ffprobe not installed")
    path = str(tmp_path / "probe.wav")
    write_wav(path, _tone(1.5))
    assert probe_media_duration(path) == pytest.approx(1.5, abs=0.05)


def test_probe_media_duration_missing_ffprobe_raises(monkeypatch, tmp_path):
    import core.multitrack_audio as mod
    monkeypatch.setattr("core.external_tools.resolve_tool", lambda name: None)
    with pytest.raises(RuntimeError):
        mod.probe_media_duration(str(tmp_path / "missing.wav"))
