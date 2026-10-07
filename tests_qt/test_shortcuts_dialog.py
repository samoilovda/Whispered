"""Help > Keyboard shortcuts lists the menu's own keys plus the extras."""

from __future__ import annotations

from core.i18n import load_locale, tr


def test_sheet_lists_menu_shortcuts_and_extras(process_events):
    load_locale("en")
    from ui.main_window import MainWindow
    from ui.shortcuts_dialog import ShortcutsDialog, collect_shortcuts

    window = MainWindow()
    groups = dict(collect_shortcuts(window.menuBar()))
    playback = dict(groups[tr("menu_playback")])
    assert tr("menu_add_bookmark") in playback
    assert tr("shortcut_palette") in dict(groups[tr("shortcuts_elsewhere")])
    dialog = ShortcutsDialog(window.menuBar(), window)
    dialog.show()
    process_events()
    dialog.close()
    window.close()
    process_events()
