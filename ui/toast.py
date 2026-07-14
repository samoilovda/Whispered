"""
Whispered – Toast Notifications
Non-blocking overlay notifications (success / info / error).
"""

from __future__ import annotations

from typing import Literal

from PyQt6.QtWidgets import QLabel, QWidget
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve

from ui.theme import get_theme, rgba

ToastKind = Literal["success", "info", "error", "warning"]


def _colors() -> dict[ToastKind, tuple[str, str]]:
    """Border/background color per toast kind, resolved from the active
    theme so a toast raised after a theme switch looks right."""
    t = get_theme()
    return {
        "success": (t.success, rgba(t.success, 0.12)),
        "info": (t.accent, rgba(t.accent, 0.12)),
        "warning": (t.warning, rgba(t.warning, 0.12)),
        "error": (t.error, rgba(t.error, 0.12)),
    }


_ICONS: dict[ToastKind, str] = {
    "success": "✓",
    "info":    "ℹ",
    "warning": "⚠",
    "error":   "✕",
}


class Toast(QLabel):
    """Single transient notification label rendered over the parent widget."""

    def __init__(self, message: str, kind: ToastKind = "info",
                 parent: QWidget | None = None, duration_ms: int = 3000):
        super().__init__(parent)
        colors = _colors()
        border_color, bg_color = colors.get(kind, colors["info"])
        icon = _ICONS.get(kind, "ℹ")

        self.setText(f"  {icon}  {message}  ")
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 8px;
                color: {border_color};
                font-size: 13px;
                font-weight: bold;
                padding: 8px 16px;
            }}
        """)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.adjustSize()

        if parent:
            self._reposition()

        # Fade-out animation
        self._anim = QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(400)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self.deleteLater)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(duration_ms)
        self._hide_timer.timeout.connect(self._anim.start)

        self.show()
        self._hide_timer.start()

    def _reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        x = (parent.width() - self.width()) // 2
        y = parent.height() - self.height() - 60
        self.move(x, max(y, 0))

    def resizeEvent(self, event):
        super().resizeEvent(event)

    # Allow parent resize to reposition toast
    def showEvent(self, event):
        super().showEvent(event)
        self._reposition()


def show_toast(parent: QWidget, message: str,
               kind: ToastKind = "info", duration_ms: int = 3000) -> Toast:
    """Create and display a toast on *parent*. Returns the Toast widget."""
    t = Toast(message, kind=kind, parent=parent, duration_ms=duration_ms)
    t.raise_()
    return t
