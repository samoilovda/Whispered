"""Video-frame photo slots: focus-point crop + inspector gating."""

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QImage

from covers.renderer import render
from covers.template import load_template
from ui.cover_view import CoverView


def _tall_png(tmp_path):
    image = QImage(200, 800, QImage.Format.Format_RGB32)
    image.fill(QColor("black"))
    for y in range(400):
        for x in range(200):
            image.setPixelColor(x, y, QColor("red"))
    path = tmp_path / "frame.png"
    image.save(str(path))
    return str(path)


def test_focus_point_shifts_the_crop(tmp_path):
    template = load_template("prosvet_16x9")
    src = _tall_png(tmp_path)
    top, _ = render(
        template, "solo", "mint",
        {"photo_a": {"file": src, "focus_x": 0.5, "focus_y": 0.0}},
        QSize(1280, 720),
    )
    bottom, _ = render(
        template, "solo", "mint",
        {"photo_a": {"file": src, "focus_x": 0.5, "focus_y": 1.0}},
        QSize(1280, 720),
    )

    def _bytes(img):
        return img.constBits().asstring(img.sizeInBytes())

    assert _bytes(top) != _bytes(bottom)


def test_set_video_source_gates_frame_buttons(qt_application):
    view = CoverView()
    assert not view.inspector._frame_buttons[0].isEnabled()

    view.set_video_source("/some/clip.mp4")
    assert view.inspector._frame_buttons[0].isEnabled()

    view.set_video_source("/some/audio.mp3")
    assert not view.inspector._frame_buttons[0].isEnabled()

    view.set_video_source(None)
    assert not view.inspector._frame_buttons[0].isEnabled()


def test_photo_slots_merges_focus(qt_application):
    view = CoverView()
    view.photos["photo_a"] = "/x.png"
    assert view._photo_slots()["photo_a"] == "/x.png"

    view._on_focus_changed("photo_a", 0.5, 0.15)
    merged = view._photo_slots()["photo_a"]
    assert merged == {"file": "/x.png", "focus_x": 0.5, "focus_y": 0.15}
