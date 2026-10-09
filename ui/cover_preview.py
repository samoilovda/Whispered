"""The Cover workspace's preview, where speaker photos are framed by hand.

Drag inside a photo slot to move its photo; scroll over it to zoom
around the cursor. The widget only translates mouse positions into
canvas coordinates (the 1280x720 cover) and reports what happened —
``CoverView`` turns that into the slot's focus/zoom via
``covers.photo_pan`` and redraws.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel

from covers.photo_pan import Box, slot_at


class CoverPreview(QLabel):
    """A label showing the cover scaled to fit, with photo slots you can
    drag and wheel-zoom."""

    # slot, dx, dy — canvas pixels since the last move
    photo_dragged = pyqtSignal(str, float, float)
    # slot, zoom factor, cursor x/y in canvas pixels from the slot's corner
    photo_zoomed = pyqtSignal(str, float, float, float)
    # slot — the drag ended (or a wheel burst paused): time to keep it
    framing_done = pyqtSignal(str)

    WHEEL_STEP = 1.1          # zoom factor per wheel notch

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMouseTracking(True)
        self._canvas = (1280.0, 720.0)
        self._boxes: dict[str, Box] = {}
        # Only slots that hold a photo can be moved.
        self._movable: set[str] = set()
        self._hover: Optional[str] = None
        self._dragging: Optional[str] = None
        self._last: Optional[QPointF] = None

    def set_photo_slots(self, boxes: dict[str, Box], movable: set[str],
                        canvas: tuple[float, float] = (1280.0, 720.0)) -> None:
        self._boxes = dict(boxes)
        self._movable = set(movable) & set(boxes)
        self._canvas = canvas
        if self._hover not in self._movable:
            self._hover = None
            self._update_cursor()
        self.update()

    # ── Geometry ───────────────────────────────────────────────────────

    def image_rect(self) -> QRectF:
        """Where the scaled cover is drawn inside the label."""
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return QRectF()
        ratio = pixmap.devicePixelRatio() or 1.0
        width, height = pixmap.width() / ratio, pixmap.height() / ratio
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def _scale(self) -> float:
        rect = self.image_rect()
        return rect.width() / self._canvas[0] if rect.width() > 0 else 0.0

    def to_canvas(self, point: QPointF) -> Optional[QPointF]:
        """*point* in the label → canvas pixels, or None off the picture."""
        rect = self.image_rect()
        scale = self._scale()
        if scale <= 0 or not rect.contains(point):
            return None
        return QPointF((point.x() - rect.x()) / scale, (point.y() - rect.y()) / scale)

    def _slot_under(self, point: QPointF) -> Optional[str]:
        canvas = self.to_canvas(point)
        if canvas is None:
            return None
        slot = slot_at(self._boxes, canvas.x(), canvas.y())
        return slot if slot in self._movable else None

    # ── Mouse ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        slot = self._slot_under(event.position())
        if event.button() == Qt.MouseButton.LeftButton and slot is not None:
            self._dragging = slot
            self._last = event.position()
            self._update_cursor()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        position = event.position()
        if self._dragging is not None and self._last is not None:
            scale = self._scale()
            if scale > 0:
                dx = (position.x() - self._last.x()) / scale
                dy = (position.y() - self._last.y()) / scale
                if dx or dy:
                    self.photo_dragged.emit(self._dragging, dx, dy)
            self._last = position
            event.accept()
            return
        hover = self._slot_under(position)
        if hover != self._hover:
            self._hover = hover
            self._update_cursor()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging is not None and event.button() == Qt.MouseButton.LeftButton:
            slot = self._dragging
            self._dragging = None
            self._last = None
            self._update_cursor()
            self.framing_done.emit(slot)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        slot = self._slot_under(event.position())
        canvas = self.to_canvas(event.position())
        steps = event.angleDelta().y() / 120.0
        if slot is None or canvas is None or not steps:
            super().wheelEvent(event)
            return
        x, y, _w, _h = self._boxes[slot]
        self.photo_zoomed.emit(slot, self.WHEEL_STEP ** steps, canvas.x() - x, canvas.y() - y)
        event.accept()

    def leaveEvent(self, event) -> None:
        if self._hover is not None and self._dragging is None:
            self._hover = None
            self._update_cursor()
            self.update()
        super().leaveEvent(event)

    def _update_cursor(self) -> None:
        if self._dragging is not None:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._hover is not None:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    # ── Painting ───────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        slot = self._dragging or self._hover
        if slot is None or slot not in self._boxes:
            return
        rect = self.image_rect()
        scale = self._scale()
        x, y, w, h = self._boxes[slot]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(self.palette().highlight().color()))
        pen.setWidthF(2.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(
            QRectF(rect.x() + x * scale, rect.y() + y * scale, w * scale, h * scale), 8, 8
        )
        painter.end()

    def setPixmap(self, pixmap: QPixmap) -> None:  # noqa: N802 (Qt override)
        super().setPixmap(pixmap)
        self.update()
