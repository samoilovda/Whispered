"""Speaker photo variants in the Cover workspace: the dialog shows the
cached variants per participant, and a pick fills the slot framed by the
face (covers/speaker_photos.py)."""

from __future__ import annotations

from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QPushButton

from covers.face_vision import Face
from covers.speaker_photos import CandidateSet, PhotoCandidate, save_candidates
from ui.cover_candidates_dialog import SpeakerPhotoDialog


def _photo(path, color=0x808080) -> str:
    image = QImage(640, 348, QImage.Format.Format_RGB888)
    image.fill(color)
    image.save(str(path))
    return str(path)


def _cached_set(tmp_path) -> CandidateSet:
    candidates = [
        PhotoCandidate(participant, 100.0 * (i + 1) + participant,
                       _photo(tmp_path / f"p{participant}-{i}.png"), 0.5,
                       Face(0.416, 0.460, 0.157, 0.289, 0.5))
        for participant in (0, 1) for i in range(4)
    ]
    result = CandidateSet(video="/rec/video1.mp4", candidates=candidates,
                          sampled=[100.0, 200.0], rounds=1)
    save_candidates(tmp_path, result)
    return result


def test_dialog_shows_cached_variants_without_searching(tmp_path, monkeypatch):
    _cached_set(tmp_path)
    started = []
    monkeypatch.setattr(SpeakerPhotoDialog, "_search", lambda self: started.append(True))
    dialog = SpeakerPhotoDialog("/rec/video1.mp4", 1000.0, str(tmp_path))
    thumbs = [w for w in dialog.findChildren(QPushButton) if not w.icon().isNull()]
    assert len(thumbs) == 8
    assert started == []
    thumbs[5].click()
    assert dialog.selected is not None and dialog.selected.participant == 1


def test_dialog_searches_when_nothing_is_cached(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(SpeakerPhotoDialog, "_search", lambda self: started.append(True))
    SpeakerPhotoDialog("/rec/video1.mp4", 1000.0, str(tmp_path))
    assert started == [True]


def test_picked_variant_is_framed_by_the_face(tmp_path):
    from ui.cover_view import CoverView

    candidate = _cached_set(tmp_path).candidates[0]
    view = CoverView()
    index = view.inspector.layout_combo.findData("duo")
    view.inspector.layout_combo.setCurrentIndex(index)
    view.apply_photo_candidate("photo_b", candidate)
    focus, zoom = view.photo_framing("photo_b")
    assert view.photos["photo_b"] == candidate.path
    assert zoom > 1.2 and focus[1] > 0.8          # small, low face: zoomed in, moved up
    # Moving only the zoom slider keeps the face-based focal point.
    framing = view.inspector.framing["photo_b"]
    assert framing.framing()[0] == focus
    framing.zoom_slider.setValue(framing.zoom_slider.value() + 10)
    assert view.photo_framing("photo_b")[0] == focus
