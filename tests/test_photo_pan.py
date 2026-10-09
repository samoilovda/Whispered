"""covers/photo_pan.py — dragging and wheel-zooming a cover photo; checked
against the renderer's own placement rule (covers.renderer._draw_fitted)."""

import types

import pytest

from covers.photo_pan import pan, photo_boxes, slot_at, zoom_around

SLOT = (386.0, 380.0)
IMAGE = (640.0, 348.0)


def _placement(focus, zoom, slot=SLOT, image=IMAGE):
    """Left/top of the drawn photo inside the slot and its drawn size."""
    base = max(slot[0] / image[0], slot[1] / image[1]) * zoom
    w, h = image[0] * base, image[1] * base
    return focus[0] * (slot[0] - w), focus[1] * (slot[1] - h), w, h


def test_drag_moves_the_photo_with_the_cursor():
    focus, zoom = (0.5, 0.5), 1.5
    x0, y0, _w, _h = _placement(focus, zoom)
    moved = pan(focus, zoom, SLOT, IMAGE, dx=20, dy=-15)
    x1, y1, _w, _h = _placement(moved, zoom)
    assert x1 - x0 == pytest.approx(20, abs=0.5)
    assert y1 - y0 == pytest.approx(-15, abs=0.5)


def test_drag_stops_at_the_photo_edge():
    # Dragging far right/down shows the photo's left/top edge, no further.
    assert pan((0.5, 0.5), 1.5, SLOT, IMAGE, dx=10_000, dy=10_000) == (0.0, 0.0)
    assert pan((0.5, 0.5), 1.5, SLOT, IMAGE, dx=-10_000, dy=-10_000) == (1.0, 1.0)


def test_wide_photo_at_plain_fit_cannot_move_vertically():
    # A 16:9 Zoom tile fills a square slot's height exactly at zoom 1.
    assert pan((0.5, 0.5), 1.0, SLOT, IMAGE, dx=0, dy=40)[1] == 0.5


def test_wheel_zoom_keeps_the_point_under_the_cursor():
    focus, zoom = (0.5, 0.5), 1.2
    anchor = (120.0, 90.0)
    x0, y0, w0, h0 = _placement(focus, zoom)
    u, v = (anchor[0] - x0) / w0, (anchor[1] - y0) / h0
    new_focus, new_zoom = zoom_around(focus, zoom, 1.8, SLOT, IMAGE, anchor)
    assert new_zoom == 1.8
    x1, y1, w1, h1 = _placement(new_focus, new_zoom)
    assert x1 + u * w1 == pytest.approx(anchor[0], abs=0.5)
    assert y1 + v * h1 == pytest.approx(anchor[1], abs=0.5)


def test_wheel_zoom_is_clamped():
    assert zoom_around((0.5, 0.5), 2.4, 9.0, SLOT, IMAGE, (10, 10))[1] == 2.5
    assert zoom_around((0.5, 0.5), 1.1, 0.2, SLOT, IMAGE, (10, 10))[1] == 1.0


def test_photo_boxes_and_hit_test():
    layer = lambda kind, **data: types.SimpleNamespace(type=kind, data=data)  # noqa: E731
    template = types.SimpleNamespace(layouts={"duo": types.SimpleNamespace(layers=[
        layer("round_rect", box=[150, 108, 386, 380]),
        layer("photo", slot="photo_a", box=[165, 96, 386, 380]),
        layer("photo", slot="photo_b", box=[706, 100, 366, 386]),
    ])})
    boxes = photo_boxes(template, "duo")
    assert boxes == {"photo_a": (165, 96, 386, 380), "photo_b": (706, 100, 366, 386)}
    assert slot_at(boxes, 300, 300) == "photo_a"
    assert slot_at(boxes, 800, 200) == "photo_b"
    assert slot_at(boxes, 640, 600) is None
    assert photo_boxes(template, "solo") == {}
