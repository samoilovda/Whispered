"""
Whispered - Animated Button
A button wrapper providing smooth hover transitions via a visual overlay.
"""

from PyQt6.QtWidgets import QPushButton
from PyQt6.QtCore import QVariantAnimation, Qt
from PyQt6.QtGui import QPainter, QColor

class AnimatedButton(QPushButton):
    """A QPushButton that smoothly animates a subtle overlay on hover.
    This approach works on top of any QSS styling without conflicting with it.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hover_progress = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_progress_changed)

    def _on_progress_changed(self, value):
        self._hover_progress = value
        self.update()

    def enterEvent(self, event):
        self._anim.setDirection(QVariantAnimation.Direction.Forward)
        if self._anim.state() != QVariantAnimation.State.Running:
            self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._anim.setDirection(QVariantAnimation.Direction.Backward)
        if self._anim.state() != QVariantAnimation.State.Running:
            self._anim.start()
        super().leaveEvent(event)

    def paintEvent(self, event):
        # Draw the standard QSS-styled button first
        super().paintEvent(event)
        
        # Draw the animated overlay
        if self._hover_progress > 0 and self.isEnabled() and not self.isChecked():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            # Use the current text color for the overlay to naturally adapt to themes
            # e.g., in dark theme text is white -> light overlay.
            # in light theme text is dark -> dark overlay.
            text_color = self.palette().color(self.foregroundRole())
            overlay = QColor(text_color)
            overlay.setAlpha(int(self._hover_progress * 25)) # Max alpha 25/255
            
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(overlay)
            
            # Draw with rounded corners (assuming ~8px radius from theme)
            painter.drawRoundedRect(self.rect(), 8, 8)
            painter.end()
