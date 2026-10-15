"""Diarization: the model-free parts of diarizer.py and domain/diarization.py."""

import numpy as np
import pytest

import diarizer
from diarizer import relabel, spectral_labels, split_pieces, to_result
from domain.diarization import DiarizationResult, SpeakerSegment, assign_speakers
from domain.transcription import Segment


def _voices(sizes, dim=64, spread=0.35, seed=1):
    """Unit embeddings scattered around one random centre per voice."""
    rng = np.random.default_rng(seed)
    rows, truth = [], []
    for voice, size in enumerate(sizes):
        centre = rng.normal(size=dim)
        for _ in range(size):
            v = centre / np.linalg.norm(centre) + spread * rng.normal(size=dim) / np.sqrt(dim)
            rows.append(v / np.linalg.norm(v))
            truth.append(voice)
    return np.asarray(rows), np.asarray(truth)


def _agrees(labels, truth):
    """Same partition, whatever the label numbers."""
    pairs = set(zip(labels.tolist(), truth.tolist()))
    return len(pairs) == len(set(labels.tolist())) == len(set(truth.tolist()))


# ---------------------------------------------------------------- pieces

def test_split_pieces_cuts_long_turns_and_skips_short_ones():
    turns = [(0.0, 14.0, 0), (20.0, 20.5, 1), (30.0, 36.5, 0)]
    pieces = split_pieces(turns)
    assert pieces == [(0, 0.0, 6.0), (0, 6.0, 12.0), (0, 12.0, 14.0), (2, 30.0, 36.5)]


def test_split_pieces_folds_a_short_remainder_into_the_last_piece():
    assert split_pieces([(0.0, 12.4, 0)]) == [(0, 0.0, 6.0), (0, 6.0, 12.4)]


# ---------------------------------------------------------------- clustering

@pytest.mark.parametrize("sizes", [(60, 25), (40, 30, 20), (30, 30, 30, 15)])
def test_spectral_labels_finds_the_number_of_voices(sizes):
    emb, truth = _voices(sizes)
    labels = spectral_labels(emb)
    assert _agrees(labels, truth)


def test_spectral_labels_keeps_a_single_voice_as_one_speaker():
    emb, _truth = _voices((80,), spread=1.0)
    assert set(spectral_labels(emb).tolist()) == {0}


def test_spectral_labels_honours_a_given_speaker_count():
    emb, truth = _voices((50, 20))
    labels = spectral_labels(emb, num_speakers=2)
    assert _agrees(labels, truth)


def test_spectral_labels_with_too_few_pieces_is_one_speaker():
    emb, _truth = _voices((2, 2))
    assert set(spectral_labels(emb).tolist()) == {0}


def test_relabel_gives_pieceless_turns_their_clusters_majority_label():
    turns = [(0.0, 7.0, 5), (8.0, 8.4, 5), (9.0, 9.3, 9), (10.0, 13.0, 6)]
    pieces = split_pieces(turns)  # turn 0 → two pieces, turn 3 → one
    out = relabel(turns, pieces, np.asarray([1, 1, 0]))
    # turn 1 shares cluster 5 with turn 0; turn 2's cluster has no pieces,
    # so it follows the nearest piece (turn 3's).
    assert out == [(0.0, 7.0, 1), (8.0, 8.4, 1), (9.0, 9.3, 0), (10.0, 13.0, 0)]


def test_relabel_keeps_pieces_apart_when_a_turn_has_no_clear_majority():
    turns = [(0.0, 12.0, 0), (20.0, 38.0, 0)]
    pieces = split_pieces(turns)  # 2 + 3 pieces
    out = relabel(turns, pieces, np.asarray([0, 1, 0, 0, 1]))
    assert out == [(0.0, 6.0, 0), (6.0, 12.0, 1), (20.0, 38.0, 0)]


def test_to_result_names_speakers_in_order_of_appearance_and_joins_turns():
    res = to_result([(5.0, 6.0, 3), (0.0, 2.0, 7), (2.2, 4.0, 7), (9.0, 10.0, 7)], 12.0)
    assert [(s.start, s.end, s.speaker) for s in res.segments] == [
        (0.0, 4.0, "Speaker 1"), (5.0, 6.0, "Speaker 2"), (9.0, 10.0, "Speaker 1"),
    ]
    assert res.num_speakers == 2
    assert res.duration == 12.0


def test_diarize_reports_unavailable_without_sherpa(monkeypatch):
    monkeypatch.setattr(diarizer.importlib.util, "find_spec", lambda name: None)
    assert diarizer.Diarizer().is_available() is False


# ---------------------------------------------------------------- merge into transcript

def _diar(*turns):
    return DiarizationResult(
        segments=[SpeakerSegment(s, e, spk) for s, e, spk in turns],
        num_speakers=len({t[2] for t in turns}), duration=100.0,
    )


def test_assign_speakers_picks_whoever_talks_most_in_the_segment():
    # Midpoint 5.0 falls in B's short interjection; A says most of it.
    diar = _diar((0.0, 4.8, "A"), (4.8, 5.4, "B"), (5.4, 10.0, "A"))
    seg = Segment(0.0, 10.0, "text")
    assert assign_speakers([seg], diar) == 1
    assert seg.speaker == "A"


def test_assign_speakers_uses_the_nearest_speech_for_a_segment_in_a_gap():
    diar = _diar((0.0, 2.0, "A"), (10.0, 12.0, "B"))
    early, late = Segment(3.0, 4.0, "x"), Segment(8.0, 9.5, "y")
    assign_speakers([early, late], diar)
    assert (early.speaker, late.speaker) == ("A", "B")


def test_assign_speakers_without_speech_leaves_segments_unlabelled():
    seg = Segment(0.0, 1.0, "x", speaker="old")
    assert assign_speakers([seg], DiarizationResult([], 0, 0.0)) == 0
    assert seg.speaker is None
