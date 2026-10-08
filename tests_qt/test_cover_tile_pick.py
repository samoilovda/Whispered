"""A still from a video call goes through "whose photo is this?": the
chosen participant's picture — not the whole call frame — fills the slot."""

from __future__ import annotations

import numpy as np
from PyQt6.QtGui import QImage

from ui.cover_tile_dialog import SpeakerTileDialog, image_to_rgb, speaker_crops


def _call_frame(path) -> str:
    rng = np.random.default_rng(7)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[180:540, 0:640] = 28
    frame[180:540, 218:420] = rng.integers(60, 230, (360, 202, 3))
    frame[180:540, 640:1280] = rng.integers(20, 240, (360, 640, 3))
    image = QImage(frame.data, 1280, 720, 1280 * 3, QImage.Format.Format_RGB888).copy()
    image.save(str(path))
    return str(path)


def test_image_round_trips_to_numpy(tmp_path):
    image = QImage(_call_frame(tmp_path / "f.png"))
    rgb = image_to_rgb(image)
    assert rgb.shape == (720, 1280, 3)
    assert len(speaker_crops(image)) == 2


def test_frame_grab_keeps_only_the_chosen_participant(tmp_path, monkeypatch):
    from ui.cover_view import CoverView

    def pick_second(dialog):
        dialog.selected = 1
        return SpeakerTileDialog.DialogCode.Accepted

    monkeypatch.setattr(SpeakerTileDialog, "exec", pick_second)
    view = CoverView()
    view._on_frame_ready("photo_b", _call_frame(tmp_path / "still.png"))
    chosen = QImage(view.photos["photo_b"])
    assert (chosen.width(), chosen.height()) == (640, 360)


def test_whole_frame_when_the_user_declines(tmp_path, monkeypatch):
    from ui.cover_view import CoverView

    monkeypatch.setattr(
        SpeakerTileDialog, "exec", lambda dialog: SpeakerTileDialog.DialogCode.Rejected)
    view = CoverView()
    still = _call_frame(tmp_path / "still.png")
    view._on_frame_ready("photo_b", still)
    assert view.photos["photo_b"] == still
