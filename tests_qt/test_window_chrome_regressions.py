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


def test_settings_shortcut_opens_settings_dialog(monkeypatch, process_events):
    """Settings remain reachable after retiring the inspector rail (B6) —
    now via the Ctrl+, shortcut / File menu / command palette, all of
    which call MainWindow._open_settings()."""
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

    window._open_settings()
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


def test_go_article_switches_to_the_record_page_it_lives_on(process_events):
    """Regression: Go > Article set main_tabs' current widget without
    leaving the start screen, so the tab widget it addresses was not on
    screen and the menu item looked like it did nothing."""
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.resize(1200, 800)
    window._show_new_draft()
    process_events()

    window._menu_show_articles()
    process_events()

    assert window._stack.currentIndex() == window._record_index
    assert window.main_tabs.currentWidget() is window.article_view
    assert window.article_view.isVisible()

    window.close()
    process_events()


def test_go_library_expands_a_collapsed_library_before_focusing_search(
    process_events, monkeypatch, tmp_path,
):
    """Regression: with the Library collapsed the action focused a search
    field that is not on screen, so typing went into an invisible widget."""
    import config
    from ui.main_window import MainWindow

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())

    window = MainWindow()
    window.show()
    window.resize(1200, 800)
    window.workspace_shell.set_library_collapsed(True)
    process_events()
    assert not window.library_view.isVisible()

    window._menu_focus_library()
    process_events()

    assert window.library_view.isVisible()
    assert window.library_view._search_edit.hasFocus()

    window.close()
    process_events()


def test_open_file_action_lands_on_the_page_the_file_selector_is_on(
    process_events, monkeypatch,
):
    """Regression: Ctrl+O opened the file dialog from any screen, feeding
    a file selector the user could not see."""
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.resize(1200, 800)
    process_events()

    clicked = []
    monkeypatch.setattr(
        window.file_selector.browse_btn, "click", lambda: clicked.append(True)
    )
    window._stack.setCurrentIndex(window._record_index)
    window._menu_open_file()
    process_events()

    assert window._stack.currentIndex() == window._start_index
    assert window.start_view.current_source() == "file"
    assert clicked == [True]

    window.close()
    process_events()


def test_dropping_a_file_lands_on_the_screen_that_can_launch_it(
    process_events, tmp_path,
):
    """Regression: the drop was accepted window-wide but filled a selector
    on the start screen. Dropping while a record was open left the user
    looking at that record with nothing visibly changed."""
    from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PyQt6.QtGui import QDropEvent
    from ui.main_window import MainWindow

    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"\0" * 32)

    window = MainWindow()
    window.show()
    window.resize(1200, 800)
    window._stack.setCurrentIndex(window._record_index)
    process_events()

    payload = QMimeData()
    payload.setUrls([QUrl.fromLocalFile(str(clip))])
    window.dropEvent(
        QDropEvent(
            QPointF(10, 10),
            Qt.DropAction.CopyAction,
            payload,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    process_events()

    assert window._stack.currentIndex() == window._start_index
    assert window.start_view.current_source() == "file"
    assert window.file_selector.get_file() == str(clip)
    assert window.transcribe_btn.isEnabled()

    window.close()
    process_events()

