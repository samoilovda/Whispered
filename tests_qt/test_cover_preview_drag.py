"""Framing a speaker photo by hand on the Cover preview: drag to move it
in its frame, scroll to zoom around the cursor (ui/cover_preview.py)."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QImage, QMouseEvent, QPixmap, QWheelEvent

from ui.cover_preview import CoverPreview

BOXES = {"photo_a": (165.0, 96.0, 386.0, 380.0), "photo_b": (706.0, 100.0, 366.0, 386.0)}


def _mouse(widget, kind, x, y, buttons=Qt.MouseButton.LeftButton):
    button = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton
    event = QMouseEvent(kind, QPointF(x, y), QPointF(x, y), button, buttons,
                        Qt.KeyboardModifier.NoModifier)
    widget.event(event)


def _preview(qtbot=None) -> CoverPreview:
    preview = CoverPreview()
    preview.resize(640, 400)                 # cover drawn 640x360, 20 px bands
    preview.setPixmap(QPixmap(640, 360))
    preview.set_photo_slots(BOXES, {"photo_a", "photo_b"})
    return preview


def test_label_points_map_to_canvas_pixels():
    preview = _preview()
    canvas = preview.to_canvas(QPointF(320, 200))
    assert (round(canvas.x()), round(canvas.y())) == (640, 360)
    assert preview.to_canvas(QPointF(320, 5)) is None          # band above


def test_drag_reports_canvas_deltas_and_the_end():
    preview = _preview()
    moves, done = [], []
    preview.photo_dragged.connect(lambda *a: moves.append(a))
    preview.framing_done.connect(done.append)
    # photo_a's centre: canvas (358, 286) → label (179, 163).
    _mouse(preview, QEvent.Type.MouseButtonPress, 179, 163)
    _mouse(preview, QEvent.Type.MouseMove, 189, 158, Qt.MouseButton.LeftButton)
    _mouse(preview, QEvent.Type.MouseButtonRelease, 189, 158, Qt.MouseButton.NoButton)
    assert moves == [("photo_a", 20.0, -10.0)]
    assert done == ["photo_a"]


def test_empty_slot_and_background_do_not_drag():
    preview = _preview()
    preview.set_photo_slots(BOXES, {"photo_b"})
    moves = []
    preview.photo_dragged.connect(lambda *a: moves.append(a))
    _mouse(preview, QEvent.Type.MouseButtonPress, 179, 163)   # photo_a: no photo
    _mouse(preview, QEvent.Type.MouseMove, 199, 163, Qt.MouseButton.LeftButton)
    assert moves == []


def test_wheel_zooms_around_the_cursor():
    preview = _preview()
    zooms = []
    preview.photo_zoomed.connect(lambda *a: zooms.append(a))
    event = QWheelEvent(QPointF(179, 163), QPointF(179, 163), QPoint(0, 0), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    preview.wheelEvent(event)
    slot, factor, ax, ay = zooms[0]
    assert slot == "photo_a" and factor > 1.0
    assert (round(ax), round(ay)) == (193, 190)               # from the slot's corner


def test_dragging_on_the_cover_view_moves_and_keeps_the_photo(tmp_path):
    from ui.cover_view import CoverView

    photo = QImage(640, 348, QImage.Format.Format_RGB888)
    photo.fill(0x406080)
    path = str(tmp_path / "guest.png")
    photo.save(path)
    view = CoverView()
    view.inspector.layout_combo.setCurrentIndex(view.inspector.layout_combo.findData("duo"))
    view.photos["photo_b"] = path
    view._zoom["photo_b"] = 1.6
    view.render_preview()
    assert "photo_b" in view.preview._movable

    view._on_photo_dragged("photo_b", 0.0, 30.0)      # pull the picture down
    focus, zoom = view.photo_framing("photo_b")
    assert focus[1] < 0.5 and zoom == 1.6
    view._on_photo_framed("photo_b")
    assert view.inspector.framing["photo_b"].framing() == (focus, zoom)

    view._on_photo_zoomed("photo_b", 1.25, 183.0, 193.0)
    assert view.photo_framing("photo_b")[1] == 2.0
