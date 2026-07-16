"""
Whispered – Record View
Detail screen for one open transcription: player, result tabs, and an
Export menu (replaces the old always-visible 7 format checkboxes).
"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMenu, QSplitter
from PyQt6.QtCore import Qt, pyqtSignal

from config import get_config, save_config
from core.i18n import tr
from exporters import EXPORT_FORMATS
from ui.icons import get_icon, IconColors
from ui.animated_button import AnimatedButton

# Order the Export menu lists formats in (subset of EXPORT_FORMATS that
# makes sense as a persisted multi-select; matches the old checkbox order).
_FORMAT_KEYS = ("txt", "srt", "vtt", "json", "md", "html", "docx")


class RecordView(QWidget):
    """Hosts the player + result tabs for one open transcription record.

    MainWindow still owns the actual player/tabs widgets and adds them to
    this view's layout (they're created once in MainWindow and reused
    across records, same as before the redesign) — RecordView itself only
    owns the header chrome: back button, title, and the Export menu.
    """

    back_requested = pyqtSignal()
    export_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 16, 20, 20)
        self._layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)

        back_btn = AnimatedButton(f"←  {tr('sidebar_library')}")
        back_btn.setProperty("variant", "ghost")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self.back_requested.emit)
        header.addWidget(back_btn)

        self.title_label = QLabel("")
        self.title_label.setProperty("role", "title")
        header.addWidget(self.title_label, stretch=1)

        self.export_btn = AnimatedButton(tr("record_export_menu"))
        self.export_btn.setIcon(get_icon('save', IconColors.DEFAULT, 14))
        self._build_export_menu()
        header.addWidget(self.export_btn)

        self._layout.addLayout(header)

        # Main Splitter for Content vs Tools
        self._content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter.setHandleWidth(1)

        # Left side will contain Player and Main Tabs
        self._left_widget = QWidget()
        self._left_layout = QVBoxLayout(self._left_widget)
        self._left_layout.setContentsMargins(0, 0, 0, 0)
        self._left_layout.setSpacing(4)

        self._content_splitter.addWidget(self._left_widget)

        self._layout.addWidget(self._content_splitter, stretch=1)

    def _build_export_menu(self) -> None:
        cfg = get_config()
        selected = set(getattr(cfg, "export_formats", None) or ["txt"])

        menu = QMenu(self.export_btn)
        self._format_actions = {}
        for key in _FORMAT_KEYS:
            name, _ = EXPORT_FORMATS[key]
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(key in selected)
            act.toggled.connect(lambda checked, k=key: self._on_format_toggled(k, checked))
            self._format_actions[key] = act

        menu.addSeparator()
        export_act = menu.addAction(tr("record_export_action"))
        export_act.triggered.connect(self.export_requested.emit)

        self.export_btn.setMenu(menu)

    def _on_format_toggled(self, key: str, checked: bool) -> None:
        cfg = get_config()
        formats = list(getattr(cfg, "export_formats", None) or [])
        if checked and key not in formats:
            formats.append(key)
        elif not checked and key in formats:
            formats.remove(key)
        cfg.export_formats = formats
        save_config()

    def set_content_widgets(self, player: QWidget, main_tabs: QWidget, tools_tabs: QWidget) -> None:
        """Set the player and split tab widgets owned by MainWindow."""
        self._left_layout.addWidget(player)
        self._left_layout.addWidget(main_tabs, stretch=1)

        self._content_splitter.addWidget(tools_tabs)

        # The tools tabs ask for ~900px (the YouTube/Book panels are wide)
        # and report that as their minimum too, which would pin the pane
        # open and squeeze the transcript. Cap both sides to a workable
        # minimum so the splitter can honour the ratio below instead of
        # the children's size hints.
        self._left_widget.setMinimumWidth(400)
        tools_tabs.setMinimumWidth(360)

        # Give the left (player + transcript) side a 2:1 share. Stretch
        # factors alone only govern how *extra* space is handed out on
        # resize — the initial split comes from size hints, so set it.
        self._content_splitter.setStretchFactor(0, 2)
        self._content_splitter.setStretchFactor(1, 1)
        self._content_splitter.setSizes([2000, 1000])

    def set_title(self, name: str) -> None:
        self.title_label.setText(name)

    def get_export_formats(self) -> list[str]:
        """Currently-checked formats, falling back to ['txt'] if none."""
        formats = getattr(get_config(), "export_formats", None) or []
        return formats if formats else ["txt"]
