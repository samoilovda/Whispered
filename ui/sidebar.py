"""Responsive signed sidebar for top-level application navigation."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QGraphicsDropShadowEffect,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from ui.icons import IconColors, get_icon

COMPACT_WIDTH = 64
EXPANDED_WIDTH = 200
_ICON_SIZE = 20
_BUTTON_HEIGHT = 40

_SECTIONS = (
    ("library", "list", "sidebar_library"),
    ("queue", "layers", "sidebar_queue"),
    ("recorder", "microphone", "sidebar_recorder"),
    ("live", "music", "sidebar_live"),
)


class Sidebar(QWidget):
    """Expanded labels on roomy windows, compact accessible rail otherwise."""

    section_changed = pyqtSignal(str)
    settings_requested = pyqtSignal()
    collapsed_changed = pyqtSignal(bool)

    def __init__(
        self,
        parent=None,
        *,
        live_enabled: bool = False,
        collapsed: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        self._collapsed = bool(collapsed)
        self._live_enabled = bool(live_enabled)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(12)
        shadow.setColor(QColor(0, 0, 0, 50))
        shadow.setOffset(2, 0)
        self.setGraphicsEffect(shadow)
        self._buttons: dict[str, QToolButton] = {}
        self._setup_ui()
        self.set_collapsed(self._collapsed, emit=False)
        self.set_live_enabled(live_enabled)

    def _setup_ui(self) -> None:
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 16, 10, 16)
        self._layout.setSpacing(4)

        self.brand_label = QLabel("Whispered")
        self.brand_label.setProperty("role", "section-title")
        self.brand_label.setContentsMargins(8, 0, 0, 12)
        self._layout.addWidget(self.brand_label)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for key, icon_name, label_key in _SECTIONS:
            label = tr(label_key)
            button = self._make_nav_button(icon_name, label)
            button.clicked.connect(
                lambda _checked, section=key: self.section_changed.emit(section)
            )
            self._group.addButton(button)
            self._buttons[key] = button
            self._layout.addWidget(button)

        self._layout.addStretch()
        self.settings_button = self._make_nav_button(
            "settings", tr("tooltip_settings"), checkable=False
        )
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self._layout.addWidget(self.settings_button)

        self.collapse_button = QToolButton()
        self.collapse_button.setProperty("role", "nav-button")
        self.collapse_button.setText(tr("sidebar_collapse"))
        self.collapse_button.setToolTip(tr("sidebar_collapse"))
        self.collapse_button.setAccessibleName(tr("sidebar_collapse"))
        self.collapse_button.setFixedHeight(32)
        self.collapse_button.clicked.connect(
            lambda: self.set_collapsed(not self._collapsed)
        )
        self._layout.addWidget(self.collapse_button)
        self._buttons["library"].setChecked(True)

    def _make_nav_button(
        self, icon_name: str, label: str, checkable: bool = True
    ) -> QToolButton:
        button = QToolButton()
        button.setProperty("role", "nav-button")
        button.setCheckable(checkable)
        button.setIcon(get_icon(icon_name, IconColors.DEFAULT, _ICON_SIZE))
        button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        button.setText(label)
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.setFixedHeight(_BUTTON_HEIGHT)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def set_collapsed(self, collapsed: bool, *, emit: bool = True) -> None:
        collapsed = bool(collapsed)
        self._collapsed = collapsed
        self.setFixedWidth(COMPACT_WIDTH if collapsed else EXPANDED_WIDTH)
        style = (
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if collapsed
            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        for button in (*self._buttons.values(), self.settings_button):
            button.setToolButtonStyle(style)
        self.brand_label.setText("W" if collapsed else "Whispered")
        self.brand_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter if collapsed else Qt.AlignmentFlag.AlignLeft
        )
        self.collapse_button.setText("›" if collapsed else "‹")
        self.collapse_button.setToolTip(
            tr("sidebar_expand") if collapsed else tr("sidebar_collapse")
        )
        self.collapse_button.setAccessibleName(self.collapse_button.toolTip())
        if emit:
            self.collapsed_changed.emit(collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_live_enabled(self, enabled: bool) -> None:
        self._live_enabled = bool(enabled)
        self._buttons["live"].setVisible(self._live_enabled)
        if not self._live_enabled and self._buttons["live"].isChecked():
            self.set_active("library")
            self.section_changed.emit("library")

    def set_active(self, key: str) -> None:
        button = self._buttons.get(key)
        if button is not None and button.isVisible():
            button.setChecked(True)
