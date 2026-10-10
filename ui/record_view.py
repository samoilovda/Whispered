"""
Whispered – Record View
Detail screen for one open transcription: header, result tabs, player.

Header, left to right: the record's name (click to rename in place), a
chip summarising its last recipe run, Export, and an overflow menu for
the rarer record actions (transcript versions, cover, rename, show the
files, delete). Generation is not started from here: each material's own
tab offers to create it while it doesn't exist yet.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QFocusEvent, QKeyEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config import get_config, save_config
from core.i18n import tr
from ui.i18n_helpers import Retranslator
from domain.export_preset import BUILTIN_EXPORT_PRESETS
from exporters import EXPORT_FORMATS
from ui.icons import get_icon, IconColors
from ui.animated_button import AnimatedButton
from ui.components import KeepOpenMenu
from ui.qt_util import must

# Order the Export menu lists formats in.  Keep every implemented exporter
# reachable from the workspace; the redesign previously hid timestamped TXT
# and PDF even though both were production-ready.
_FORMAT_KEYS = ("txt", "txt_ts", "srt", "vtt", "json", "md", "html", "docx", "pdf")


class _TitleEdit(QLineEdit):
    """The record's name as a page title that becomes a text field on
    click: Enter commits, Escape (or leaving an unchanged field) reverts."""

    committed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", "title-edit")
        self.setFrame(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._shown = ""
        self.editingFinished.connect(self._commit)

    def set_shown_text(self, text: str) -> None:
        self._shown = text
        self.setText(text)
        self.setCursorPosition(0)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802
        if event is not None and event.key() == Qt.Key.Key_Escape:
            self.setText(self._shown)
            self.clearFocus()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event: QFocusEvent | None) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.setCursorPosition(0)

    def _commit(self) -> None:
        text = self.text().strip()
        if not text:
            self.setText(self._shown)
        elif text != self._shown:
            self._shown = text
            self.committed.emit(text)
        self.clearFocus()


class RecordView(QWidget):
    """Hosts the player + result tabs for one open transcription record.

    MainWindow owns the actual player/tabs widgets and adds them to this
    view's layout (created once and reused across records) — RecordView
    owns only the header chrome.
    """

    back_requested = pyqtSignal()
    export_requested = pyqtSignal()
    export_preset_requested = pyqtSignal(str)  # ExportPreset.key
    cover_requested = pyqtSignal()
    versions_requested = pyqtSignal()
    rename_requested = pyqtSignal(str)
    reveal_requested = pyqtSignal()
    delete_requested = pyqtSignal()
    run_summary_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._initial_split_done = False
        self._has_result = False
        self._has_record = False
        self._i18n = Retranslator()
        self._setup_ui()
        self._i18n.call(self._retranslate_record)
        self._i18n.bind()

    def _retranslate_record(self) -> None:
        self.title_edit.setToolTip(tr("record_title_tooltip"))
        self.cover_action.setText(tr("record_cover_action"))
        self.versions_action.setText(tr("record_versions_action"))
        self.rename_action.setText(tr("library_rename"))
        self.reveal_action.setText(tr("record_reveal_files"))
        self.delete_action.setText(tr("history_delete_title"))
        self.more_btn.setToolTip(tr("record_more_actions"))
        self.more_btn.setAccessibleName(tr("record_more_actions"))
        self.export_btn.setText(tr("record_export_menu"))
        self.set_has_result(self._has_result)
        self._build_export_menu()

    def _setup_ui(self) -> None:
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(20, 16, 20, 20)
        self._layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.title_edit = _TitleEdit()
        self.title_edit.committed.connect(self.rename_requested.emit)
        title_box.addWidget(self.title_edit)
        # When, how long, which language and model — the record's facts.
        self.subtitle_label = QLabel()
        self.subtitle_label.setProperty("role", "muted")
        self.subtitle_label.setVisible(False)
        title_box.addWidget(self.subtitle_label)
        header.addLayout(title_box, stretch=1)

        # Last run of a recipe on this record: what succeeded, what failed.
        self.run_chip = QPushButton()
        self.run_chip.setProperty("role", "run-chip")
        self.run_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_chip.clicked.connect(self.run_summary_clicked.emit)
        self.run_chip.setVisible(False)
        header.addWidget(self.run_chip)

        self.export_btn = AnimatedButton(tr("record_export_menu"))
        self.export_btn.setIcon(get_icon('save', IconColors.default(), 14))
        self._build_export_menu()
        header.addWidget(self.export_btn)

        self.more_btn = QToolButton()
        self.more_btn.setProperty("role", "toolbar-icon")
        self.more_btn.setIcon(get_icon("more_horizontal", IconColors.default(), 16))
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more = QMenu(self.more_btn)
        # B8, docs/IMPROVEMENT_PLAN_2026-08.ru.md: non-destructive
        # transcript edit history (MainWindow owns the dialog/restore).
        self.versions_action = QAction(tr("record_versions_action"), more)
        self.versions_action.triggered.connect(self.versions_requested.emit)
        more.addAction(self.versions_action)
        # The Cover workspace opened with this record's segments loaded
        # (docs/IMPROVEMENT_PLAN_2026-08.ru.md, A5).
        self.cover_action = QAction(tr("record_cover_action"), more)
        self.cover_action.triggered.connect(self.cover_requested.emit)
        more.addAction(self.cover_action)
        more.addSeparator()
        self.rename_action = QAction(tr("library_rename"), more)
        self.rename_action.triggered.connect(self.start_rename)
        more.addAction(self.rename_action)
        self.reveal_action = QAction(tr("record_reveal_files"), more)
        self.reveal_action.triggered.connect(self.reveal_requested.emit)
        more.addAction(self.reveal_action)
        more.addSeparator()
        self.delete_action = QAction(tr("history_delete_title"), more)
        self.delete_action.triggered.connect(self.delete_requested.emit)
        more.addAction(self.delete_action)
        self.more_btn.setMenu(more)
        header.addWidget(self.more_btn)

        self._layout.addLayout(header)

        # RecordView owns document content only — every generator's panel
        # is one more tab on main_tabs (see MainWindow._build_record_section).
        self._left_widget = QWidget()
        self._left_layout = QVBoxLayout(self._left_widget)
        self._left_layout.setContentsMargins(0, 0, 0, 0)
        self._left_layout.setSpacing(4)

        self._layout.addWidget(self._left_widget, stretch=1)
        self.set_has_result(False)
        self.set_has_record(False)

    def _build_export_menu(self) -> None:
        cfg = get_config()
        selected = set(getattr(cfg, "export_formats", None) or ["txt"])

        menu = KeepOpenMenu(self.export_btn)

        # Presets first (B9, docs/IMPROVEMENT_PLAN_2026-08.ru.md item 3) —
        # "collect the YouTube package" in one click, formats below a
        # separator for the existing pick-your-own-formats flow.
        for preset in BUILTIN_EXPORT_PRESETS:
            act = must(menu.addAction(tr(f"export_preset_{preset.key}")))
            act.triggered.connect(
                lambda _checked=False, key=preset.key: self.export_preset_requested.emit(key)
            )
        menu.addSeparator()

        self._format_actions = {}
        for key in _FORMAT_KEYS:
            name, _ = EXPORT_FORMATS[key]
            act = must(menu.addAction(name))
            act.setCheckable(True)
            act.setChecked(key in selected)
            act.toggled.connect(lambda checked, k=key: self._on_format_toggled(k, checked))
            self._format_actions[key] = act

        menu.addSeparator()
        export_act = must(menu.addAction(tr("record_export_action")))
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
        self.title_edit.set_shown_text(name)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))

    def title(self) -> str:
        return self.title_edit.text()

    def start_rename(self) -> None:
        self.title_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.title_edit.selectAll()

    def set_run_summary(self, text: str, tooltip: str = "", clickable: bool = False) -> None:
        """Show the last-run chip (empty *text* hides it). *clickable*
        when clicking leads somewhere (an active run, or one to resume)."""
        self.run_chip.setText(text)
        self.run_chip.setToolTip(tooltip)
        self.run_chip.setVisible(bool(text))
        self.run_chip.setEnabled(clickable)
        self.run_chip.setCursor(
            Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor
        )

    def set_has_record(self, has_record: bool) -> None:
        """Whether the open result is a saved history record — renaming,
        showing its files and deleting need one."""
        self._has_record = has_record
        self.title_edit.setReadOnly(not has_record)
        self.rename_action.setEnabled(has_record)
        self.reveal_action.setEnabled(has_record)
        self.delete_action.setEnabled(has_record)

    def set_has_result(self, has_result: bool) -> None:
        self._has_result = has_result
        self.cover_action.setEnabled(has_result)
        self.versions_action.setEnabled(has_result)
        self.export_btn.setEnabled(has_result)
        disabled_tip = "" if has_result else tr("tooltip_record_actions_disabled")
        self.export_btn.setToolTip(disabled_tip)

    def get_export_formats(self) -> list[str]:
        """Currently-checked formats, falling back to ['txt'] if none."""
        formats = getattr(get_config(), "export_formats", None) or []
        return formats if formats else ["txt"]
