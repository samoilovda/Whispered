"""Regression tests for window chrome and compact workspace controls."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog


def test_workspace_has_no_internal_top_header(process_events):
    """Content begins with the workspace; native window chrome stays native."""
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.resize(1100, 700)
    process_events()

    shell = window.workspace_shell
    assert not hasattr(shell, "header_bar")
    assert shell.layout().count() == 1
    assert shell.layout().itemAt(0).widget() is shell.splitter

    window.close()
    process_events()


def test_inspector_settings_button_opens_settings_dialog(monkeypatch, process_events):
    """Settings remain reachable after removing the internal top header."""
    from ui.main_window import MainWindow
    from ui.settings_dialog import SettingsDialog

    opened = []

    def fake_exec(dialog):
        opened.append(dialog)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    window = MainWindow()
    window.show()
    process_events()

    window.inspector.settings_button.click()
    process_events()

    assert len(opened) == 1
    assert opened[0].parent() is window

    window.close()
    process_events()


def test_compact_library_action_uses_icon_without_clipped_text(process_events):
    """At minimum width the Library action is an icon, not a crushed label."""
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.resize(900, 550)
    process_events()

    button = window.workspace_shell.new_button
    assert window.library_view.isVisible() is False
    assert button.text() == ""
    assert not button.icon().isNull()
    assert button.accessibleName()
    assert button.toolTip()

    window.resize(1100, 700)
    process_events()
    assert button.text()
    assert button.icon().isNull()

    window.close()
    process_events()


def test_find_menu_action_opens_search_only_on_record_page(process_events):
    """The native Find action must target the transcript instead of a dead shim."""
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    window._trigger_find()
    assert window.transcript_view._find_bar.isHidden()

    window._stack.setCurrentIndex(window._record_index)
    window._trigger_find()
    process_events()
    assert window.transcript_view._find_bar.isVisible()
    assert window.transcript_view._find_edit.hasFocus()

    window.close()
    process_events()
