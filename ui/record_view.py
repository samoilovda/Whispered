"""
Whispered – Record View
Detail screen for one open transcription: player, result tabs, and an
Export menu (replaces the old always-visible 7 format checkboxes).
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import pyqtSignal

from config import get_config, save_config
from core.i18n import tr
from exporters import EXPORT_FORMATS
from ui.icons import get_icon, IconColors
from ui.animated_button import AnimatedButton
from ui.components import KeepOpenMenu

# Order the Export menu lists formats in.  Keep every implemented exporter
# reachable from the workspace; the redesign previously hid timestamped TXT
# and PDF even though both were production-ready.
_FORMAT_KEYS = ("txt", "txt_ts", "srt", "vtt", "json", "md", "html", "docx", "pdf")


class RecordView(QWidget):
    """Hosts the player + result tabs for one open transcription record.

    MainWindow still owns the actual player/tabs widgets and adds them to
    this view's layout (they're created once in MainWindow and reused
    across records, same as before the redesign) — RecordView itself only
    owns the header chrome: back button, title, and the Export menu.
    """

    back_requested = pyqtSignal()
    export_requested = pyqtSignal()
    clean_requested = pyqtSignal()
    articles_requested = pyqtSignal()
    cover_requested = pyqtSignal()
    versions_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._initial_split_done = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 16, 20, 20)
        self._layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)

        self.title_label = QLabel("")
        self.title_label.setProperty("role", "page-title")
        header.addWidget(self.title_label, stretch=1)

        self.clean_btn = AnimatedButton(tr("record_clean_action"))
        self.clean_btn.setEnabled(False)
        self.clean_btn.setToolTip(tr("tooltip_record_actions_disabled"))
        self.clean_btn.clicked.connect(self.clean_requested.emit)
        header.addWidget(self.clean_btn)

        self.articles_btn = AnimatedButton(tr("record_articles_action"))
        self.articles_btn.setEnabled(False)
        self.articles_btn.setToolTip(tr("tooltip_record_actions_disabled"))
        self.articles_btn.clicked.connect(self.articles_requested.emit)
        header.addWidget(self.articles_btn)

        # Third entrance to the Cover workspace (docs/IMPROVEMENT_PLAN_2026-08.ru.md,
        # A5) — the other two are the Library panel's own button and
        # Go > Covers on the menu bar. This one opens it with the open
        # record's segments already loaded, since Cover is made *for* a
        # record rather than being its own destination.
        self.cover_btn = AnimatedButton(tr("record_cover_action"))
        self.cover_btn.setEnabled(False)
        self.cover_btn.setToolTip(tr("tooltip_record_actions_disabled"))
        self.cover_btn.clicked.connect(self.cover_requested.emit)
        header.addWidget(self.cover_btn)

        # B8, docs/IMPROVEMENT_PLAN_2026-08.ru.md: non-destructive
        # transcript edit history — opens TranscriptVersionsDialog
        # (MainWindow owns the actual dialog/restore wiring).
        self.versions_btn = AnimatedButton(tr("record_versions_action"))
        self.versions_btn.setEnabled(False)
        self.versions_btn.setToolTip(tr("tooltip_record_actions_disabled"))
        self.versions_btn.clicked.connect(self.versions_requested.emit)
        header.addWidget(self.versions_btn)

        self.export_btn = AnimatedButton(tr("record_export_menu"))
        self.export_btn.setIcon(get_icon('save', IconColors.default(), 14))
        self._build_export_menu()
        header.addWidget(self.export_btn)

        self._layout.addLayout(header)

        # RecordView owns document content only — every generator's panel
        # is one more tab on main_tabs (see MainWindow._build_record_section).
        self._left_widget = QWidget()
        self._left_layout = QVBoxLayout(self._left_widget)
        self._left_layout.setContentsMargins(0, 0, 0, 0)
        self._left_layout.setSpacing(4)

        self._layout.addWidget(self._left_widget, stretch=1)

    def _build_export_menu(self) -> None:
        cfg = get_config()
        selected = set(getattr(cfg, "export_formats", None) or ["txt"])

        menu = KeepOpenMenu(self.export_btn)
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

    def set_content_widgets(self, player: QWidget, main_tabs: QWidget) -> None:
        """Set the player and the tabbed document/generator content."""
        self._left_layout.addWidget(main_tabs, stretch=1)

        from ui.components import apply_soft_shadow
        player.setProperty("role", "card")
        apply_soft_shadow(player)

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(40, 0, 40, 16)
        layout.addWidget(player)
        self._left_layout.addWidget(container)

    def set_title(self, name: str) -> None:
        self.title_label.setText(name)

    def set_has_result(self, has_result: bool) -> None:
        self.clean_btn.setEnabled(has_result)
        self.articles_btn.setEnabled(has_result)
        self.cover_btn.setEnabled(has_result)
        self.versions_btn.setEnabled(has_result)
        disabled_tip = "" if has_result else tr("tooltip_record_actions_disabled")
        self.clean_btn.setToolTip(disabled_tip)
        self.articles_btn.setToolTip(disabled_tip)
        self.cover_btn.setToolTip(disabled_tip)
        self.versions_btn.setToolTip(disabled_tip)

    def get_export_formats(self) -> list[str]:
        """Currently-checked formats, falling back to ['txt'] if none."""
        formats = getattr(get_config(), "export_formats", None) or []
        return formats if formats else ["txt"]
