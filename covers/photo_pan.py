"""Moving and zooming a cover photo inside its slot by hand.

The renderer (covers.renderer._draw_fitted) draws a "cover"-fit photo at
``base * zoom`` scale, where ``base = max(slot_w / image_w, slot_h /
image_h)``, and places it so that photo point ``focus`` lines up with the
same relative point of the slot: the photo's left edge sits at
``focus_x * (slot_w - drawn_w)`` from the slot's left. These helpers turn
a drag (pixels on the 1280x720 canvas) or a wheel step at the cursor
into the ``focus``/``zoom`` that produce exactly that picture.

Qt-free so the math is unit-tested without a display.
"""

from __future__ import annotations

from typing import Optional

# covers.renderer.MAX_PHOTO_ZOOM — kept here without importing Qt.
MAX_ZOOM = 2.5

Box = tuple[float, float, float, float]          # x, y, w, h on the canvas


def _drawn_size(
    slot: tuple[float, float], image: tuple[float, float], zoom: float,
) -> tuple[float, float]:
    slot_w, slot_h = slot
    image_w, image_h = image
    base = max(slot_w / image_w, slot_h / image_h) * min(MAX_ZOOM, max(1.0, zoom))
    return image_w * base, image_h * base


def _focus_for_offset(offset: float, slot_len: float, drawn_len: float) -> float:
    """The focus that puts the photo's edge at *offset* from the slot's
    edge; the photo always covers the slot, so it is clamped to 0..1."""
    room = slot_len - drawn_len
    if abs(room) < 1e-6:
        return 0.5
    return min(1.0, max(0.0, offset / room))


def pan(
    focus: tuple[float, float], zoom: float,
    slot: tuple[float, float], image: tuple[float, float],
    dx: float, dy: float,
) -> tuple[float, float]:
    """*focus* after dragging the photo by (*dx*, *dy*) canvas pixels: the
    picture follows the cursor until its edge meets the slot's edge."""
    drawn_w, drawn_h = _drawn_size(slot, image, zoom)
    x0 = focus[0] * (slot[0] - drawn_w) + dx
    y0 = focus[1] * (slot[1] - drawn_h) + dy
    return (
        round(_focus_for_offset(x0, slot[0], drawn_w), 4),
        round(_focus_for_offset(y0, slot[1], drawn_h), 4),
    )


def zoom_around(
    focus: tuple[float, float], zoom: float, new_zoom: float,
    slot: tuple[float, float], image: tuple[float, float],
    anchor: tuple[float, float],
) -> tuple[tuple[float, float], float]:
    """``(focus, zoom)`` after zooming to *new_zoom* with the photo point
    under *anchor* (canvas pixels from the slot's top-left) staying put —
    the face under the cursor stays under it."""
    new_zoom = min(MAX_ZOOM, max(1.0, new_zoom))
    old_w, old_h = _drawn_size(slot, image, zoom)
    new_w, new_h = _drawn_size(slot, image, new_zoom)
    x0 = focus[0] * (slot[0] - old_w)
    y0 = focus[1] * (slot[1] - old_h)
    # The photo point under the anchor, as a fraction of the photo.
    u = (anchor[0] - x0) / old_w
    v = (anchor[1] - y0) / old_h
    return (
        round(_focus_for_offset(anchor[0] - u * new_w, slot[0], new_w), 4),
        round(_focus_for_offset(anchor[1] - v * new_h, slot[1], new_h), 4),
    ), round(new_zoom, 4)


def photo_boxes(template, layout: str) -> dict[str, Box]:
    """Each photo slot's box on the canvas in *layout* of *template*."""
    spec = template.layouts.get(layout) if hasattr(template.layouts, "get") else None
    boxes: dict[str, Box] = {}
    if spec is None:
        return boxes
    for layer in spec.layers:
        data = getattr(layer, "data", {}) or {}
        box = data.get("box") or []
        if getattr(layer, "type", "") == "photo" and data.get("slot") and len(box) == 4:
            boxes[str(data["slot"])] = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    return boxes


def slot_at(boxes: dict[str, Box], x: float, y: float) -> Optional[str]:
    """The photo slot under canvas point (*x*, *y*), if any."""
    for slot, (bx, by, bw, bh) in boxes.items():
        if bx <= x <= bx + bw and by <= y <= by + bh:
            return slot
    return None
