"""Unit tests for L5 per-source VAD; no audio device or ML dependency."""

from __future__ import annotations

from array import array

import pytest

from core.live import AudioFrame, SpeechTurn
from core.live.vad import EnergyVAD, PerSourceVAD, VADConfig, pcm_rms


_RATE = 16_000
_FRAME_SAMPLES = 1_600  # 100 ms


def _frame(
    sequence: int,
    timestamp: float,
    amplitude: int = 0,
    *,
    source: str = "mic",
    discontinuity=False,
):
    pcm = array("h", [amplitude] * _FRAME_SAMPLES).tobytes()
    return AudioFrame(
        source, sequence, timestamp, timestamp, _RATE, pcm, discontinuity=discontinuity
    )


def _config(**overrides):
    values = dict(
        speech_threshold=0.02,
        silence_threshold=0.01,
        end_silence_seconds=0.6,
        pre_roll_seconds=0.2,
        post_roll_seconds=0.2,
        max_turn_seconds=1.5,
    )
    values.update(overrides)
    return VADConfig(**values)


def test_pcm_rms_and_energy_backend_are_deterministic():
    assert pcm_rms(b"") == 0.0
    assert pcm_rms(array("h", [0, 0]).tobytes()) == 0.0
    assert pcm_rms(array("h", [16_384, -16_384]).tobytes()) == pytest.approx(0.5)
    assert EnergyVAD(0.2)(_frame(0, 0.0, 10_000)) is True
    assert EnergyVAD(0.2)(_frame(0, 0.0, 1_000)) is False


def test_short_confirmation_is_not_dropped_and_pre_roll_is_kept():
    vad = PerSourceVAD("mic", _config())
    turns: list[SpeechTurn] = []

    # 200 ms quiet pre-roll, one 100 ms spoken confirmation, then 600 ms quiet.
    for seq in range(2):
        turns.extend(vad.feed(_frame(seq, seq * 0.1)))
    turns.extend(vad.feed(_frame(2, 0.2, 8_000)))
    for seq in range(3, 9):
        turns.extend(vad.feed(_frame(seq, seq * 0.1)))

    assert len(turns) == 1
    turn = turns[0]
    assert (turn.first_sequence, turn.last_sequence) == (0, 5)
    assert turn.start == pytest.approx(0.0)
    assert turn.end == pytest.approx(0.5)


def test_hysteresis_keeps_quiet_speech_inside_one_turn():
    vad = PerSourceVAD("mic", _config(end_silence_seconds=0.4))
    turns: list[SpeechTurn] = []

    for seq, amplitude in enumerate((8_000, 500, 500, 8_000)):
        turns.extend(vad.feed(_frame(seq, seq * 0.1, amplitude)))
    turns.extend(vad.feed(_frame(4, 0.4)))
    turns.extend(vad.feed(_frame(5, 0.5)))
    turns.extend(vad.feed(_frame(6, 0.6)))
    turns.extend(vad.feed(_frame(7, 0.7)))

    assert len(turns) == 1
    assert turns[0].first_sequence == 0
    assert turns[0].last_sequence == 6


def test_loud_continuous_audio_is_split_at_max_duration():
    vad = PerSourceVAD("mic", _config(max_turn_seconds=0.5))
    turns: list[SpeechTurn] = []

    for seq in range(15):
        turns.extend(vad.feed(_frame(seq, seq * 0.1, 12_000)))
    turns.extend(vad.flush())

    assert len(turns) == 3
    assert all(turn.end - turn.start <= 0.5 + 1e-9 for turn in turns)
    assert [(turn.first_sequence, turn.last_sequence) for turn in turns] == [
        (0, 4),
        (5, 9),
        (10, 14),
    ]


def test_discontinuity_closes_current_turn_and_marks_next_speech():
    vad = PerSourceVAD("mic", _config())
    assert vad.feed(_frame(0, 0.0, 8_000)) == []
    turns = vad.feed(_frame(4, 0.4, 8_000, discontinuity=True))
    assert len(turns) == 1
    assert turns[0].discontinuity is True

    vad.feed(_frame(5, 0.5))
    vad.feed(_frame(6, 0.6, 8_000))
    next_turn = vad.flush()[0]
    assert next_turn.discontinuity is True


def test_source_isolation_and_invalid_pcm_are_explicit():
    vad = PerSourceVAD("mic")
    with pytest.raises(ValueError, match="for 'mic'"):
        vad.feed(_frame(0, 0.0, source="system"))

    with pytest.raises(ValueError, match="even number"):
        pcm_rms(b"odd")
