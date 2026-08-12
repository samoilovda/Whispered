"""Persistent icon rail and content stack for record tools."""

from __future__ import annotations

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config import get_config, save_config
from core.i18n import tr
from ui.icons import IconColors, get_icon


_SECTIONS = (
    ("materials", "list", "inspector_materials"),
    ("insights", "fire", "inspector_insights"),
    ("cut", "layers", "inspector_cut"),
    ("chat", "user", "inspector_chat"),
    ("settings", "settings", "inspector_settings"),
)


class InspectorRail(QWidget):
    """Five stable inspector destinations with a collapsible content pane."""

    section_changed = pyqtSignal(str)
    collapsed_changed = pyqtSignal(bool)
    settings_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        self._buttons: dict[str, QToolButton] = {}
        self._pages: dict[str, QWidget] = {}
        self._artifact_ready: set[str] = set()
        self._setup_ui()
        cfg = get_config()
        section = getattr(cfg, "inspector_section", "materials")
        self.set_section(section if section in self._buttons else "materials")
        self.set_collapsed(getattr(cfg, "inspector_collapsed", False), emit=False)

    def _setup_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        rail = QWidget()
        rail.setProperty("role", "inspector-rail")
        rail.setFixedWidth(52)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(6, 10, 6, 10)
        rail_layout.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for key, icon_name, label_key in _SECTIONS:
            button = QToolButton()
            button.setProperty("role", "nav-button")
            button.setCheckable(True)
            button.setIcon(get_icon(icon_name, IconColors.default(), 18))
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(40, 40)
            button.setToolTip(tr(label_key))
            button.setAccessibleName(tr(label_key))
            button.clicked.connect(
                lambda _checked, name=key: self._activate_section(name)
            )
            self._group.addButton(button)
            self._buttons[key] = button
            rail_layout.addWidget(button)
        rail_layout.addStretch()
        self.collapse_button = QToolButton()
        self.collapse_button.setProperty("role", "nav-button")
        self.collapse_button.setFixedSize(40, 36)
        self.collapse_button.clicked.connect(lambda: self.set_collapsed(not self.is_collapsed()))
        rail_layout.addWidget(self.collapse_button)
        root.addWidget(rail)

        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(8)
        header = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setProperty("role", "section-title")
        header.addWidget(self.title_label, stretch=1)
        self.settings_button = QToolButton()
        self.settings_button.setIcon(get_icon("settings", IconColors.default(), 16))
        self.settings_button.setToolTip(tr("tooltip_settings"))
        self.settings_button.clicked.connect(self.settings_requested.emit)
        header.addWidget(self.settings_button)
        content_layout.addLayout(header)
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, stretch=1)
        root.addWidget(self.content, stretch=1)

    def set_page(self, section: str, widget: QWidget) -> int:
        """Install or replace the content page for one rail destination."""
        old = self._pages.get(section)
        if old is not None:
            self.stack.removeWidget(old)
        self._pages[section] = widget
        index = self.stack.addWidget(widget)
        if section == self.current_section():
            self.stack.setCurrentWidget(widget)
        return index

    def addTab(self, widget: QWidget, label: str) -> int:  # noqa: N802
        """Compatibility shim for the retired ToolWorkspace API."""
        index = self.stack.addWidget(widget)
        key = next((name for name, page in self._pages.items() if page is widget), None)
        if key is None:
            key = f"legacy-{index}"
            self._pages[key] = widget
        return index

    def set_section(self, section: str) -> None:
        if section not in self._buttons:
            return
        self._buttons[section].setChecked(True)
        page = self._pages.get(section)
        if page is not None:
            self.stack.setCurrentWidget(page)
        self.title_label.setText(tr(f"inspector_{section}"))
        cfg = get_config()
        cfg.inspector_section = section
        save_config()
        self.section_changed.emit(section)

    def _activate_section(self, section: str) -> None:
        if self.is_collapsed():
            self.set_collapsed(False, emit=False)
        self.set_section(section)

    def current_section(self) -> str:
        for key, button in self._buttons.items():
            if button.isChecked():
                return key
        return "materials"

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)

    def currentIndex(self) -> int:  # noqa: N802
        return self.stack.currentIndex()

    def set_collapsed(self, collapsed: bool, *, emit: bool = True) -> None:
        collapsed = bool(collapsed)
        self.content.setVisible(not collapsed)
        self.collapse_button.setText("‹" if collapsed else "›")
        self.collapse_button.setToolTip(
            tr("inspector_expand") if collapsed else tr("inspector_collapse")
        )
        if emit:
            cfg = get_config()
            cfg.inspector_collapsed = collapsed
            save_config()
            self.collapsed_changed.emit(collapsed)

    def is_collapsed(self) -> bool:
        return self.content.isHidden()

    def set_artifacts(self, artifacts: list[str] | set[str]) -> None:
        """Expose generated-artifact readiness without relying on colour."""
        self._artifact_ready = set(artifacts)
        materials = self._buttons["materials"]
        label = tr("inspector_materials")
        if self._artifact_ready.intersection({"transcript", "youtube", "article", "book"}):
            label = f"✓ {label}"
        materials.setToolTip(label)
        materials.setAccessibleName(label)
