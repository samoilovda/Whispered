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


def _drop_event(paths, pos=(10, 10)):
    """QDropEvent doesn't take ownership of its QMimeData — pin it to the
    event itself so it outlives this function's own local, or the event
    ends up holding a dangling pointer and segfaults on mimeData() access
    the moment the caller actually reads it."""
    from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PyQt6.QtGui import QDropEvent

    payload = QMimeData()
    payload.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    event = QDropEvent(
        QPointF(*pos),
        Qt.DropAction.CopyAction,
        payload,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    event._keep_alive_payload = payload
    return event


def test_dropping_three_files_queues_all_three(process_events, tmp_path):
    """B5a acceptance criterion: dropping three audio files at once queues
    all three and opens the queue overlay — the old dropEvent only ever
    looked at urls[0] and silently lost the rest."""
    from ui.main_window import MainWindow

    clips = [tmp_path / f"clip{i}.mp3" for i in range(3)]
    for clip in clips:
        clip.write_bytes(b"\0" * 32)

    window = MainWindow()
    window.show()
    process_events()

    window.dropEvent(_drop_event(clips))
    process_events()

    assert window.batch_panel.processor.count == 3
    assert {item.filepath for item in window.batch_panel.processor.items} == {
        str(c) for c in clips
    }
    assert window.status_bar._overlay.isVisible()

    window.close()
    process_events()


def test_dropping_a_mix_of_supported_and_unsupported_files_skips_and_toasts(
    process_events, tmp_path, monkeypatch,
):
    """B5a acceptance criterion: two audio files + one .txt -> two queued
    items and a toast naming the one skipped file."""
    from ui.main_window import MainWindow

    audio = [tmp_path / "a.mp3", tmp_path / "b.wav"]
    for clip in audio:
        clip.write_bytes(b"\0" * 32)
    text_file = tmp_path / "notes.txt"
    text_file.write_text("not audio")

    toasts = []
    monkeypatch.setattr(
        "ui.main_window.show_toast",
        lambda parent, message, **kwargs: toasts.append(message),
    )

    window = MainWindow()
    window.show()
    process_events()

    window.dropEvent(_drop_event([*audio, text_file]))
    process_events()

    assert window.batch_panel.processor.count == 2
    from core.i18n import tr
    assert toasts == [tr("toast_drop_skipped_unsupported", count=1)]

    window.close()
    process_events()


def test_dropping_a_folder_expands_its_top_level_supported_files(
    process_events, tmp_path,
):
    """B5a: a dropped folder is expanded one level deep, non-recursively —
    a nested subfolder's own files must not be pulled in."""
    from ui.main_window import MainWindow

    folder = tmp_path / "session"
    folder.mkdir()
    (folder / "one.mp3").write_bytes(b"\0" * 32)
    (folder / "two.wav").write_bytes(b"\0" * 32)
    (folder / "readme.txt").write_text("not audio")
    nested = folder / "subfolder"
    nested.mkdir()
    (nested / "three.mp3").write_bytes(b"\0" * 32)

    window = MainWindow()
    window.show()
    process_events()

    window.dropEvent(_drop_event([folder]))
    process_events()

    assert window.batch_panel.processor.count == 2
    assert {item.filepath for item in window.batch_panel.processor.items} == {
        str(folder / "one.mp3"), str(folder / "two.wav"),
    }

    window.close()
    process_events()


def test_dropping_a_single_file_still_uses_the_start_screen_not_the_queue(
    process_events, tmp_path,
):
    """A lone supported file must keep the pre-B5a behavior (start screen
    + file selector), not go through the batch queue."""
    from ui.main_window import MainWindow

    clip = tmp_path / "solo.mp3"
    clip.write_bytes(b"\0" * 32)

    window = MainWindow()
    window.show()
    process_events()

    window.dropEvent(_drop_event([clip]))
    process_events()

    assert window.batch_panel.processor.count == 0
    assert window.file_selector.get_file() == str(clip)

    window.close()
    process_events()


def test_go_covers_menu_action_opens_the_cover_workspace(process_events):
    """Regression basis for docs/IMPROVEMENT_PLAN_2026-08.ru.md, A5: the
    Cover workspace used to have exactly one entrance, a button in the
    Library panel — not the menu bar, not the command palette, not the
    record screen a cover is actually made for."""
    from PyQt6.QtGui import QAction, QKeySequence
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.resize(1200, 800)
    process_events()

    cover_action = next(
        action for action in window.findChildren(QAction)
        if action.shortcut() == QKeySequence("Ctrl+4")
    )
    cover_action.trigger()
    process_events()

    assert window._stack.currentIndex() == window._cover_index

    window.close()
    process_events()


def test_record_cover_button_opens_the_cover_workspace(process_events):
    """The record screen's own Cover button (A5's third entrance)."""
    from transcriber import Segment, TranscriptionResult
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.resize(1200, 800)
    process_events()

    result = TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello world")], language="en", duration=1.0,
    )
    window._document_session.apply_result(result)
    window.record_view.set_has_result(True)
    process_events()

    assert window.record_view.cover_btn.isEnabled()
    window.record_view.cover_btn.click()
    process_events()

    assert window._stack.currentIndex() == window._cover_index

    window.close()
    process_events()


def test_record_cover_button_disabled_without_a_result(process_events):
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    window.record_view.set_has_result(False)
    process_events()
    assert not window.record_view.cover_btn.isEnabled()

    window.close()
    process_events()


def test_library_cover_button_is_icon_only(process_events):
    """Regression: with two other entrances now (menu bar, record screen),
    the Library panel's own Cover button is icon-only rather than the
    widest thing in its toolbar."""
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    btn = window.library_view._cover_btn
    assert btn.text() == ""
    assert btn.width() <= 32

    window.close()
    process_events()

