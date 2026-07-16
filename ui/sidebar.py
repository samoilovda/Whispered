"""
Whispered – Sidebar Navigation
Narrow vertical icon rail for switching between top-level sections
(Library / Queue / Recorder), with Settings pinned to the bottom.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QToolButton, QButtonGroup, QGraphicsDropShadowEffect
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor

from core.i18n import tr
from ui.icons import get_icon, IconColors

SIDEBAR_WIDTH = 56
_ICON_SIZE = 20
_BUTTON_SIZE = 40

# (key, icon name, i18n tooltip key) — order defines button order.
_SECTIONS = (
    ("library", "list", "sidebar_library"),
    ("queue", "layers", "sidebar_queue"),
    ("recorder", "microphone", "sidebar_recorder"),
)


class Sidebar(QWidget):
    """Icon-only navigation rail. Emits section_changed(key) when a nav
    button is clicked, and settings_requested() for the pinned-bottom
    settings button (which opens a dialog rather than switching pages)."""

    section_changed = pyqtSignal(str)
    settings_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "card")
        self.setFixedWidth(SIDEBAR_WIDTH)
        
        # Apply soft drop shadow casting to the right
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(12)
        shadow.setColor(QColor(0, 0, 0, 50))
        shadow.setOffset(2, 0)
        self.setGraphicsEffect(shadow)

        self._buttons: dict[str, QToolButton] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 8, 16)
        layout.setSpacing(4)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for key, icon_name, tooltip_key in _SECTIONS:
            btn = self._make_nav_button(icon_name, tr(tooltip_key))
            btn.clicked.connect(lambda _checked, k=key: self.section_changed.emit(k))
            self._group.addButton(btn)
            self._buttons[key] = btn
            layout.addWidget(btn)

        layout.addStretch()

        settings_btn = self._make_nav_button("settings", tr("tooltip_settings"), checkable=False)
        settings_btn.clicked.connect(self.settings_requested.emit)
        layout.addWidget(settings_btn)

        # Default to the Library section.
        self._buttons["library"].setChecked(True)

    def _make_nav_button(self, icon_name: str, tooltip: str, checkable: bool = True) -> QToolButton:
        btn = QToolButton()
        btn.setProperty("role", "nav-button")
        btn.setCheckable(checkable)
        btn.setIcon(get_icon(icon_name, IconColors.DEFAULT, _ICON_SIZE))
        btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        btn.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def set_active(self, key: str) -> None:
        """Programmatically mark *key* as the active section (e.g. when the
        page is switched by something other than a sidebar click)."""
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setChecked(True)
