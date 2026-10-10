"""Help > Keyboard shortcuts: every key the app answers to, in one place.

Built from the menu bar's own actions (so it can't drift from what the
keys actually do) plus the few shortcuts that live outside the menus
(Ctrl+K, Ctrl+F in the transcript, Ctrl+Enter to launch, Ctrl/⌘+Enter in
live notes). Modal and rebuilt on each open, like the other dialogs.
"""

from __future__ import annotations

from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from ui.qt_util import must

# Shortcuts that are not on a menu: (key sequence, i18n key).
_EXTRA = (
    ("Ctrl+K", "shortcut_palette"),
    ("Ctrl+F", "shortcut_find_in_transcript"),
    ("Ctrl+Return", "shortcut_launch"),
    ("Ctrl+Return", "shortcut_live_note_time"),
)


def _native(sequence: str) -> str:
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)


def collect_shortcuts(menubar) -> list[tuple[str, list[tuple[str, str]]]]:
    """(menu title, [(action text, shortcut text)]) for every menu with at
    least one shortcut, in menu-bar order; then the extras."""
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    for menu_action in menubar.actions():
        menu = menu_action.menu()
        if menu is None:
            continue
        rows = []
        for action in menu.actions():
            keys = [k for k in action.shortcuts() if not k.isEmpty()]
            if not keys or action.isSeparator():
                continue
            text = action.text().replace("&", "")
            shown = " / ".join(k.toString(QKeySequence.SequenceFormat.NativeText) for k in keys)
            rows.append((text, shown))
        if rows:
            groups.append((menu.title().replace("&", ""), rows))
    groups.append((tr("shortcuts_elsewhere"), [(tr(key), _native(seq)) for seq, key in _EXTRA]))
    return groups


class ShortcutsDialog(QDialog):
    def __init__(self, menubar, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("shortcuts_title"))
        self.resize(560, 560)
        root = QVBoxLayout(self)
        title = QLabel(tr("shortcuts_title"))
        title.setProperty("role", "page-title")
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setColumnStretch(0, 1)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        row = 0
        for group, items in collect_shortcuts(menubar):
            header = QLabel(group)
            header.setProperty("role", "list-group-header")
            grid.addWidget(header, row, 0, 1, 2)
            row += 1
            for text, keys in items:
                grid.addWidget(QLabel(text), row, 0)
                key_label = QLabel(keys)
                key_label.setProperty("role", "kbd")
                grid.addWidget(key_label, row, 1)
                row += 1
        grid.setRowStretch(row, 1)
        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox()
        # Our own caption: Qt's standard "Close" isn't translated here.
        close = must(buttons.addButton(tr("btn_close"), QDialogButtonBox.ButtonRole.RejectRole))
        close.setDefault(True)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
