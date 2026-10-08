"""Video-frame photo slots: focus-point/zoom crop + inspector gating."""

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

    view._on_framing_changed("photo_a", 0.5, 0.15, 1.0)
    merged = view._photo_slots()["photo_a"]
    assert merged == {"file": "/x.png", "focus_x": 0.5, "focus_y": 0.15}


def _dot_png(tmp_path):
    """A small green square in the middle of a white picture — the 'small
    face in a video-call tile' case."""
    image = QImage(400, 400, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    for y in range(180, 220):
        for x in range(180, 220):
            image.setPixelColor(x, y, QColor(0, 255, 0))
    path = tmp_path / "dot.png"
    image.save(str(path))
    return str(path)


def _green_pixels(image: QImage) -> int:
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    data = image.constBits().asstring(image.sizeInBytes())
    # Format_RGB32 is 0xffRRGGBB, little-endian in memory: B, G, R, A.
    return sum(
        1 for i in range(0, len(data), 4)
        if data[i] < 40 and data[i + 1] > 215 and data[i + 2] < 40
    )


def test_zoom_enlarges_the_subject_around_the_focus(tmp_path):
    template = load_template("prosvet_16x9")
    src = _dot_png(tmp_path)

    def draw(value):
        image, _ = render(template, "solo", "mint", {"photo_a": value}, QSize(1280, 720))
        return image

    plain = draw(src)
    zoomed = draw({"file": src, "focus_x": 0.5, "focus_y": 0.5, "zoom": 2.0})
    small, big = _green_pixels(plain), _green_pixels(zoomed)
    assert small > 0
    # Twice the scale: about four times the area.
    assert 3.0 < big / small < 5.0
    # Out-of-range zoom is clamped: never below the cover fit (the slot
    # stays covered), never past MAX_PHOTO_ZOOM.
    assert _bytes(draw({"file": src, "focus_x": 0.5, "focus_y": 0.5, "zoom": 0.3})) == _bytes(plain)
    assert _bytes(draw({"file": src, "focus_x": 0.5, "focus_y": 0.5, "zoom": 9})) == _bytes(
        draw({"file": src, "focus_x": 0.5, "focus_y": 0.5, "zoom": 2.5})
    )


def test_inspector_zoom_reaches_the_photo_slot(qt_application):
    view = CoverView()
    view.photos["photo_b"] = "/guest.png"
    framing = view.inspector.framing["photo_b"]
    framing.zoom_slider.setValue(180)
    assert framing.zoom_label.text() == "180 %"
    assert view._photo_slots()["photo_b"] == {
        "file": "/guest.png", "focus_x": 0.5, "focus_y": 0.5, "zoom": 1.8
    }
    framing.focus_combo.setCurrentIndex(1)  # "top"
    assert view.photo_framing("photo_b") == ((0.5, 0.15), 1.8)
    assert view.render_params()["cover_slots"]["photo_b"]["zoom"] == 1.8


def test_wizard_framing_is_mirrored_into_the_inspector(qt_application):
    view = CoverView()
    view.photos["photo_b"] = "/guest.png"
    view.set_photo_framing("photo_b", (0.5, 0.15), 2.0)
    framing = view.inspector.framing["photo_b"]
    assert framing.framing() == ((0.5, 0.15), 2.0)
    assert view._photo_slots()["photo_b"]["zoom"] == 2.0


def _bytes(img):
    return img.constBits().asstring(img.sizeInBytes())
