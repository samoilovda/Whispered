"""Speaker photo variants in the Cover workspace: one dialog for both
cover photos — a row per participant, each filling its own slot — and a
pick fills that slot framed by the face (covers/speaker_photos.py)."""

from __future__ import annotations

from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QPushButton

from covers.face_vision import Face
from covers.speaker_photos import CandidateSet, PhotoCandidate, load_candidates, save_candidates
from ui.cover_candidates_dialog import SpeakerPhotoDialog

VIDEO = "/rec/video1.mp4"


def _photo(path, color=0x808080) -> str:
    image = QImage(640, 348, QImage.Format.Format_RGB888)
    image.fill(color)
    image.save(str(path))
    return str(path)


def _cached_set(tmp_path) -> CandidateSet:
    candidates = [
        PhotoCandidate(participant, 100.0 * (i + 1) + participant,
                       _photo(tmp_path / f"p{participant}-{i}.png", 0x101010 * (i + 1) + participant),
                       0.5, Face(0.416, 0.460, 0.157, 0.289, 0.5))
        for participant in (0, 1) for i in range(4)
    ]
    result = CandidateSet(video=VIDEO, candidates=candidates, sampled=[100.0, 200.0], rounds=1)
    save_candidates(tmp_path, result)
    return result


def _thumbs(dialog) -> list[QPushButton]:
    return [w for w in dialog._grid_host.findChildren(QPushButton) if not w.icon().isNull()]


def _no_search(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(SpeakerPhotoDialog, "_search", lambda self, p: calls.append(p))
    return calls


def test_rows_fill_both_slots_at_once(tmp_path, monkeypatch):
    _cached_set(tmp_path)
    assert _no_search(monkeypatch) == []
    dialog = SpeakerPhotoDialog(VIDEO, 1000.0, str(tmp_path))
    thumbs = _thumbs(dialog)
    assert len(thumbs) == 8
    assert not dialog._apply.isEnabled()
    thumbs[1].click()      # participant 1 → left photo
    thumbs[6].click()      # participant 2 → right photo
    dialog._apply.click()
    assert dialog.choices["photo_a"].participant == 0
    assert dialog.choices["photo_b"].participant == 1


def test_one_row_only_leaves_the_other_slot_alone(tmp_path, monkeypatch):
    _cached_set(tmp_path)
    _no_search(monkeypatch)
    dialog = SpeakerPhotoDialog(VIDEO, 1000.0, str(tmp_path))
    _thumbs(dialog)[5].click()
    dialog._apply.click()
    assert set(dialog.choices) == {"photo_b"}


def test_swap_moves_participants_between_slots_and_is_kept(tmp_path, monkeypatch):
    _cached_set(tmp_path)
    _no_search(monkeypatch)
    dialog = SpeakerPhotoDialog(VIDEO, 1000.0, str(tmp_path))
    dialog._swap.click()
    _thumbs(dialog)[0].click()        # participant 1, now the right photo
    dialog._apply.click()
    assert set(dialog.choices) == {"photo_b"}
    assert load_candidates(tmp_path, VIDEO).slot_for(0) == "photo_b"


def test_more_for_one_participant_searches_just_that_row(tmp_path, monkeypatch):
    _cached_set(tmp_path)
    calls = _no_search(monkeypatch)
    dialog = SpeakerPhotoDialog(VIDEO, 1000.0, str(tmp_path))
    rows_more = [b for b in dialog._grid_host.findChildren(QPushButton) if b.icon().isNull()]
    rows_more[1].click()
    assert calls == [[1]]


def test_dialog_searches_when_nothing_is_cached(tmp_path, monkeypatch):
    calls = _no_search(monkeypatch)
    SpeakerPhotoDialog(VIDEO, 1000.0, str(tmp_path))
    assert calls == [None]


def test_picked_variants_are_framed_by_the_face(tmp_path, monkeypatch):
    from ui.cover_view import CoverView

    result = _cached_set(tmp_path)
    view = CoverView()
    view.inspector.layout_combo.setCurrentIndex(view.inspector.layout_combo.findData("duo"))
    view._video = VIDEO

    def pick_both(dialog):
        dialog.choices = {"photo_a": result.candidates[0], "photo_b": result.candidates[4]}
        return SpeakerPhotoDialog.DialogCode.Accepted

    monkeypatch.setattr(SpeakerPhotoDialog, "exec", pick_both)
    monkeypatch.setattr("ui.cover_candidates_dialog.load_candidates", lambda d, v: result)
    view._suggest_photos()
    assert view.photos["photo_a"] == result.candidates[0].path
    assert view.photos["photo_b"] == result.candidates[4].path
    focus, zoom = view.photo_framing("photo_b")
    assert zoom > 1.2 and focus[1] > 0.8          # small, low face: zoomed in, moved up
    # Moving only the zoom slider keeps the face-based focal point.
    framing = view.inspector.framing["photo_b"]
    framing.zoom_slider.setValue(framing.zoom_slider.value() + 10)
    assert view.photo_framing("photo_b")[0] == focus
