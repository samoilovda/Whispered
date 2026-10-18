"""domain.review and its storage (N01)."""

from __future__ import annotations

from types import SimpleNamespace

from core.history import HistoryStore
from domain import review as r
from domain.transcription import Segment


def _segs():
    return [
        Segment(0.0, 2.0, "sure part", speaker="A", confidence=0.95),
        Segment(2.0, 4.0, "doubtful part", speaker="A", confidence=0.40),
        Segment(4.0, 6.0, "no confidence known", speaker="B", confidence=None),
    ]


def test_low_confidence_needs_review_and_unknown_confidence_does_not():
    assert r.review_indices(_segs(), ()) == [1]


def test_reviewed_clears_it_but_keeps_the_original_confidence():
    segs = _segs()
    marks = r.with_mark((), segs[1], r.REVIEWED)
    assert r.review_indices(segs, marks) == []
    assert segs[1].confidence == 0.40
    assert r.counts(segs, marks) == (1, 0)


def test_editing_the_text_puts_a_reviewed_passage_back():
    segs = _segs()
    marks = r.with_mark((), segs[1], r.REVIEWED)
    segs[1] = Segment(2.0, 4.0, "corrected part", speaker="A", confidence=0.40)
    assert r.review_indices(segs, marks) == [1]
    assert r.holding(segs, marks) == ()


def test_changed_times_also_drop_a_reviewed_mark():
    segs = _segs()
    marks = r.with_mark((), segs[1], r.REVIEWED)
    segs[1] = Segment(2.5, 4.0, "doubtful part", speaker="A", confidence=0.40)
    assert r.review_indices(segs, marks) == [1]


def test_a_flag_survives_a_text_correction_and_adds_to_the_set():
    segs = _segs()
    marks = r.with_mark((), segs[0], r.FLAGGED)
    assert r.review_indices(segs, marks) == [0, 1]
    segs[0] = Segment(0.0, 2.0, "sure part, fixed", speaker="A", confidence=0.95)
    assert r.review_indices(segs, marks) == [0, 1]
    assert r.review_indices(segs, r.with_mark(marks, segs[0], None)) == [1]


def test_round_trip_and_newer_schema():
    segs = _segs()
    marks = r.with_mark(r.with_mark((), segs[0], r.FLAGGED), segs[1], r.REVIEWED)
    data = r.to_dict(marks, "rev")
    assert r.from_dict(data) == marks
    data["schema_version"] = 99
    assert r.from_dict(data) is None


def test_storage_round_trip_and_cleanup(tmp_path):
    store = HistoryStore(db_path=tmp_path / "h.db")
    rid = store.add(SimpleNamespace(segments=[], language="en", duration=1.0), source_path="")
    marks = r.with_mark((), _segs()[1], r.REVIEWED)
    store.save_review_marks(rid, r.to_dict(marks))
    assert r.from_dict(store.get_review_marks(rid)) == marks
    store.save_review_marks(rid, r.to_dict(()))
    assert store.get_review_marks(rid) is None
    store.save_review_marks(rid, r.to_dict(marks))
    store.delete(rid)
    assert store.get_review_marks(rid) is None
