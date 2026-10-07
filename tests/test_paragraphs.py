"""domain.paragraphs.group_paragraphs: readable paragraphs from segments."""

from domain.paragraphs import HARD_MAX_CHARS, group_paragraphs
from domain.transcription import Segment


def _segs(*items):
    """(start, end, text[, speaker]) tuples → Segments."""
    return [Segment(it[0], it[1], it[2], it[3] if len(it) > 3 else None) for it in items]


def test_a_speaker_change_starts_a_paragraph():
    segs = _segs((0, 2, "Hi.", "A"), (2, 4, "Hello.", "B"), (4, 6, "How are you?", "B"))
    paras = group_paragraphs(segs)
    assert [p.indices for p in paras] == [[0], [1, 2]]
    assert [p.speaker for p in paras] == ["A", "B"]


def test_speakers_flow_together_when_labels_are_hidden():
    segs = _segs((0, 2, "Hi.", "A"), (2, 4, "Hello.", "B"))
    assert [p.indices for p in group_paragraphs(segs, by_speaker=False)] == [[0, 1]]


def test_a_long_pause_breaks_after_enough_text():
    long_text = "word " * 20
    segs = _segs((0, 5, long_text), (9, 10, "After the pause."))
    assert [p.indices for p in group_paragraphs(segs)] == [[0], [1]]
    # …but a short interjection before the pause stays with what follows.
    segs = _segs((0, 1, "Yes."), (5, 6, "So, to continue."))
    assert [p.indices for p in group_paragraphs(segs)] == [[0, 1]]


def test_long_runs_break_at_a_sentence_end():
    sentence = "This is a sentence of moderate length that keeps going on. "
    segs = _segs(*[(i * 5, i * 5 + 5, sentence) for i in range(20)])
    paras = group_paragraphs(segs)
    assert len(paras) > 1
    assert sum(len(p.indices) for p in paras) == 20
    assert all(p.end - p.start <= 60 for p in paras)


def test_unpunctuated_speech_is_capped():
    chunk = "и вот мы говорим дальше без точек " * 3
    segs = _segs(*[(i * 3, i * 3 + 3, chunk) for i in range(60)])
    paras = group_paragraphs(segs)
    assert len(paras) > 1
    assert max(p.chars for p in paras) < HARD_MAX_CHARS + len(chunk) + 1


def test_empty_input():
    assert group_paragraphs([]) == []
