"""covers/speaker_photos.py and covers/face_vision.py — speaker photo
variants for the cover (no FFmpeg or Vision needed here)."""

import json

import pytest

from covers.face_vision import Face, parse_output
from covers.speaker_photos import (
    CandidateSet,
    PhotoCandidate,
    _pick,
    framing_for,
    load_candidates,
    sample_times,
    save_candidates,
    split_by_faces,
)
from covers.tiles import Rect


def _face_position(face, focus, zoom, slot, image):
    """Where covers.renderer._draw_fitted puts the face (slot fractions)."""
    sw, sh = slot
    iw, ih = image
    ratio = max(sw / iw, sh / ih) * zoom
    tw, th = iw * ratio, ih * ratio
    x0 = focus[0] * (sw - tw)
    y0 = focus[1] * (sh - th)
    cx, cy = face.center
    return (x0 + cx * tw) / sw, (y0 + cy * th) / sh, face.h * th / sh


def test_framing_puts_faces_at_the_same_size_and_height():
    slot, image = (386, 380), (640, 348)
    # Record 48: the host fills the tile, the guest sits low and small.
    host = Face(0.394, 0.246, 0.208, 0.383, 0.6)
    guest = Face(0.416, 0.460, 0.157, 0.289, 0.5)
    placed = []
    for face in (host, guest):
        focus, zoom = framing_for(face, slot, image)
        assert 1.0 <= zoom <= 2.5
        placed.append(_face_position(face, focus, zoom, slot, image))
    for x, y, share in placed:
        assert x == pytest.approx(0.5, abs=0.02)
        assert y == pytest.approx(0.42, abs=0.02)
        assert share == pytest.approx(0.45, abs=0.02)


def test_framing_without_a_face_is_plain_centring():
    assert framing_for(None, (386, 380), (640, 348)) == ((0.5, 0.5), 1.0)


def test_sample_times_spread_and_new_rounds_never_repeat():
    first = sample_times(1000, 10, 0)
    assert len(first) == 10
    assert min(first) >= 30 and max(first) <= 970
    second = sample_times(1000, 10, 1, taken=first)
    step = 940 / 10
    assert second and all(abs(a - b) >= step / 3 for a in second for b in first)
    assert sample_times(0, 10, 0) == []


def test_split_by_faces_cuts_touching_gallery_tiles():
    # Two equal Zoom tiles side by side seen as one strip.
    strip = [Rect(0, 180, 1280, 360)]
    faces = [Face(0.20, 0.47, 0.08, 0.14, 0.4), Face(0.68, 0.37, 0.10, 0.19, 0.5)]
    assert split_by_faces(strip, faces, (1280, 720)) == [
        Rect(0, 180, 640, 360), Rect(640, 180, 640, 360),
    ]
    assert split_by_faces(strip, faces[:1], (1280, 720)) == strip


def test_pick_keeps_the_best_moments_apart():
    scored = [(0.9, 100, 0, 0), (0.85, 110, 1, 0), (0.7, 500, 2, 0), (0.6, 900, 3, 0)]
    picked = _pick(scored, 3, min_gap=60)
    assert [item[1] for item in picked] == [100, 500, 900]


def test_parse_output_reads_faces_and_skips_errors():
    text = "\n".join([
        json.dumps({"path": "/a.png", "faces": [
            {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4, "quality": 0.5}]}),
        json.dumps({"path": "/b.png", "error": "unreadable image"}),
        "not json",
    ])
    assert parse_output(text) == {"/a.png": [Face(0.1, 0.2, 0.3, 0.4, 0.5)]}


def test_candidate_cache_round_trip_and_staleness(tmp_path):
    photo = tmp_path / "p0-00010.00.png"
    photo.write_bytes(b"png")
    result = CandidateSet(
        video="/v.mp4",
        candidates=[PhotoCandidate(0, 10.0, str(photo), 0.5, Face(0.1, 0.2, 0.3, 0.4, 0.5))],
        sampled=[10.0, 20.0], rounds=1,
    )
    save_candidates(tmp_path, result)
    loaded = load_candidates(tmp_path, "/v.mp4")
    assert loaded == result
    assert load_candidates(tmp_path, "/other.mp4") is None
    photo.unlink()
    assert load_candidates(tmp_path, "/v.mp4") is None
