from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QColor, QPen

class LoadingSpinner(QWidget):
    """An animated loading spinner widget."""
    def __init__(self, size: int = 24, color: str = "#6366f1", parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._color = QColor(color)
        self._angle = 0
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._rotate)
        self.timer.setInterval(50) # 20 fps
        self.hide() # Hidden by default
        
    def _rotate(self):
        self._angle = (self._angle + 30) % 360
        self.update()
        
    def start(self):
        self._angle = 0
        self.show()
        self.timer.start()
        
    def stop(self):
        self.timer.stop()
        self.hide()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        width = self.width()
        height = self.height()
        
        painter.translate(width / 2.0, height / 2.0)
        painter.rotate(self._angle)
        
        # Draw 12 lines in a circle
        for i in range(12):
            alpha = int(255 - (255 * i / 12))
            color = QColor(self._color)
            color.setAlpha(alpha)
            
            pen = QPen(color)
            pen.setWidth(max(2, width // 10))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            
            painter.drawLine(0, int(width / 4), 0, int(width / 2 - 2))
            painter.rotate(-30) # Rotate backwards so the head is brightest
