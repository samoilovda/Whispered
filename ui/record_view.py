"""
Whispered – Record View
Detail screen for one open transcription: player, result tabs, and an
Export menu (replaces the old always-visible 7 format checkboxes).
"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMenu
from PyQt6.QtCore import Qt, pyqtSignal

from config import get_config, save_config
from core.i18n import tr
from exporters import EXPORT_FORMATS
from ui.icons import get_icon, IconColors

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

        back_btn = QPushButton(f"←  {tr('sidebar_library')}")
        back_btn.setProperty("variant", "ghost")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self.back_requested.emit)
        header.addWidget(back_btn)

        self.title_label = QLabel("")
        self.title_label.setProperty("role", "title")
        header.addWidget(self.title_label, stretch=1)

        self.export_btn = QPushButton(tr("record_export_menu"))
        self.export_btn.setIcon(get_icon('save', IconColors.DEFAULT, 14))
        self._build_export_menu()
        header.addWidget(self.export_btn)

        self._layout.addLayout(header)

        # Player and content_tabs are inserted here by MainWindow via
        # add_content_widgets(), after this view is constructed.
        self._content_layout = QVBoxLayout()
        self._content_layout.setSpacing(4)
        self._layout.addLayout(self._content_layout, stretch=1)

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

    def add_content_widgets(self, *widgets: QWidget) -> None:
        """Add the player/content_tabs widgets MainWindow owns into this
        view's content area, in order."""
        for w in widgets:
            self._content_layout.addWidget(w)

    def set_title(self, name: str) -> None:
        self.title_label.setText(name)

    def get_export_formats(self) -> list[str]:
        """Currently-checked formats, falling back to ['txt'] if none."""
        formats = getattr(get_config(), "export_formats", None) or []
        return formats if formats else ["txt"]
