"""Discoverable materials and compact navigation (UI/UX audit 2026-10-18,
UX-10 / T10)."""

from __future__ import annotations

from PyQt6.QtCore import Qt

from core.i18n import load_locale, tr
from transcriber import Segment, TranscriptionResult


def _window(process_events, size):
    from ui.main_window import MainWindow

    load_locale("en")
    window = MainWindow()
    window.show()
    window.resize(*size)
    process_events()
    return window


def test_compact_library_and_new_record_are_separate_named_buttons(process_events):
    window = _window(process_events, (900, 550))
    shell = window.workspace_shell
    assert not shell.library_button.isHidden()
    assert shell.library_button.accessibleName() == tr("workspace_open_library")
    assert shell.new_button.accessibleName() == tr("workspace_new")
    requested = []
    shell.new_requested.connect(lambda: requested.append(True))
    shell.new_button.click()
    assert requested and not window.library_view.isVisible()  # "New" only does new
    window.close()
    process_events()


def test_library_command_reveals_a_force_hidden_library(process_events):
    window = _window(process_events, (900, 550))
    library_action = next(a for a in window._palette_menu_actions
                          if a.text() == tr("menu_go_library"))
    library_action.trigger()
    process_events()
    assert window.library_view.isVisible()
    window.close()
    process_events()


def test_add_material_is_labelled_when_wide_and_named_when_compact(process_events):
    window = _window(process_events, (1440, 900))
    window._document_session.apply_result(
        TranscriptionResult(segments=[Segment(0.0, 1.0, "hi")], language="en", duration=1.0))
    window._stack.setCurrentIndex(window._record_index)
    window.resize(1440, 900)
    process_events()
    button = window._add_tab_btn
    assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    assert button.text() == tr("record_add_material")
    # All but one material tab open, in the smallest window: whether the
    # label shows or not, it never covers or squeezes the tab names.
    for widget in window._material_tabs[:-1]:
        window._revealed_tabs.add(id(widget))
    window._refresh_tab_visibility()
    window.resize(900, 550)
    process_events()
    bar = window.main_tabs.tabBar()
    assert bar.width() >= bar.sizeHint().width()
    corner = button.mapTo(window.main_tabs, button.rect().topLeft()).x()
    assert bar.mapTo(window.main_tabs, bar.rect().topRight()).x() <= corner
    assert button.accessibleName() == tr("record_add_material")
    assert button.toolTip() == tr("record_add_material_tip")
    window.close()
    process_events()


def test_opening_a_material_tab_starts_no_generation(process_events):
    window = _window(process_events, (1100, 700))
    window._document_session.apply_result(
        TranscriptionResult(segments=[Segment(0.0, 1.0, "hi")], language="en", duration=1.0))
    window._stack.setCurrentIndex(window._record_index)
    window._refresh_tab_visibility()
    actions = window._add_tab_menu.actions()
    assert actions and all(a.toolTip() == tr("record_add_material_item_tip") for a in actions)
    actions[0].trigger()
    process_events()
    assert window._article_job is None if hasattr(window, "_article_job") else True
    assert window._book_job is None
    assert not window.recipe_run.is_running()
    window.close()
    process_events()
