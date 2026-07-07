from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRect, QPoint, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF, QPainterPath

class ProgressTimeline(QWidget):
    """A horizontal chevron-style timeline for staged progress."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.stages = [
            "Select", "Extract", "Transcribe", "Diarize", "Clean", "Generate"
        ]
        self.current_stage = 0
        self.stage_progress = 0 # 0-100 for current stage
        self.has_error = False
        self.setMinimumHeight(24)

    def set_stage(self, stage_idx: int, progress: int = 0):
        self.current_stage = stage_idx
        self.stage_progress = progress
        self.has_error = False
        self.update()

    def set_error(self, stage_idx: int):
        self.current_stage = stage_idx
        self.has_error = True
        self.update()

    def set_progress(self, progress: int):
        self.stage_progress = progress
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Colors
        bg_color = QColor("#2a2a2a")
        bg_active = QColor("#3730a3") # darker indigo
        bg_done = QColor("#166534")   # darker green
        bg_error = QColor("#7f1d1d")  # darker red

        fg_color = QColor("#888888")
        fg_active = QColor("#e0e0e0")

        prog_active = QColor("#6366f1") # bright indigo

        width = self.width()
        height = self.height()

        num_stages = len(self.stages)
        stage_width = width / num_stages

        chevron_offset = 10

        # We need to construct QPoint array for QPolygon
        for i, stage_name in enumerate(self.stages):
            x = i * stage_width

            # Determine state
            if i < self.current_stage:
                state = "done"
            elif i == self.current_stage:
                state = "error" if self.has_error else "active"
            else:
                state = "pending"

            # Create polygon for chevron
            points = []

            # Top left
            points.append(QPointF(float(x), 0.0))
            if i > 0:
                points.append(QPointF(float(x + chevron_offset), float(height / 2)))
                points.append(QPointF(float(x), float(height)))
            else:
                points.append(QPointF(float(x), float(height)))

            # Bottom right
            points.append(QPointF(float(x + stage_width), float(height)))
            if i < num_stages - 1:
                points.append(QPointF(float(x + stage_width + chevron_offset), float(height / 2)))
                points.append(QPointF(float(x + stage_width), 0.0))
            else:
                points.append(QPointF(float(x + stage_width), 0.0))

            poly = QPolygonF(points)

            # Draw background
            if state == "done":
                painter.setBrush(QBrush(bg_done))
            elif state == "active":
                painter.setBrush(QBrush(bg_active))
            elif state == "error":
                painter.setBrush(QBrush(bg_error))
            else:
                painter.setBrush(QBrush(bg_color))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPolygon(poly)

            # Draw progress fill if active
            if state == "active" and self.stage_progress > 0:
                painter.save()
                path = QPainterPath()
                path.addPolygon(poly)
                painter.setClipPath(path)
                prog_w = stage_width * (self.stage_progress / 100.0)
                if i > 0:
                    prog_w += chevron_offset
                painter.setBrush(QBrush(prog_active))
                painter.drawRect(int(x), 0, int(prog_w), int(height))
                painter.restore()

            # Draw text
            if state in ("done", "active"):
                painter.setPen(fg_active)
            else:
                painter.setPen(fg_color)

            text = stage_name
            if state == "active" and self.stage_progress > 0:
                text += f" ({self.stage_progress}%)"

            text_rect = QRect(int(x + chevron_offset), 0, int(stage_width - chevron_offset), int(height))
            if i == 0:
                text_rect = QRect(int(x), 0, int(stage_width), int(height))

            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)

            # Draw separator lines over the polygon edges to make them crisp
            if i > 0:
                pen = QPen(QColor("#1a1a1a"))
                pen.setWidth(2)
                painter.setPen(pen)
                painter.drawLine(QPoint(int(x), 0), QPoint(int(x + chevron_offset), int(height / 2)))
                painter.drawLine(QPoint(int(x + chevron_offset), int(height / 2)), QPoint(int(x), int(height)))
