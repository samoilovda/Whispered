"""Runtime UI-language switching (no application restart).

``core.i18n.set_locale()`` reloads the locale strings and fans the change
out to every widget that registered a retranslate callback. These tests
drive that path through the real widgets.
"""

import pytest

from core import i18n


@pytest.fixture(autouse=True)
def _restore_locale():
    before = i18n.current_lang()
    yield
    i18n.set_locale(before)


def test_set_locale_notifies_listeners_only_on_real_change():
    i18n.set_locale("en")
    seen = []
    cb = lambda: seen.append(i18n.current_lang())  # noqa: E731
    i18n.on_language_changed(cb)
    try:
        assert i18n.set_locale("ru") is True
        assert i18n.set_locale("ru") is False        # no-op, no broadcast
        assert i18n.set_locale("en") is True
        assert seen == ["ru", "en"]
    finally:
        i18n.remove_language_listener(cb)


def test_dead_listener_is_dropped_silently():
    i18n.set_locale("en")

    class Ephemeral:
        def __init__(self):
            self.hits = 0

        def bump(self):
            self.hits += 1

    obj = Ephemeral()
    i18n.on_language_changed(obj.bump)
    del obj
    # Must not raise even though the only strong ref is gone.
    i18n.set_locale("ru")


def test_main_window_menus_and_tabs_retranslate(process_events):
    from ui.main_window import MainWindow

    i18n.set_locale("en")
    window = MainWindow()
    process_events()

    en_tab = window.main_tabs.tabText(0)
    en_menu = window._i18n_menu_items[0][0].title()

    i18n.set_locale("ru")
    process_events()

    assert window.main_tabs.tabText(0) != en_tab
    assert window._i18n_menu_items[0][0].title() != en_menu
    assert window.main_tabs.tabText(0) == i18n.tr("tab_transcript")

    window.close()
    process_events()


def test_every_panel_survives_repeated_switches(process_events):
    """Realise the full window (all generator tabs, live panels, library)
    and bounce the language back and forth — no retranslate closure may
    raise, and captions must actually track the active language."""
    from ui.main_window import MainWindow

    i18n.set_locale("en")
    window = MainWindow()
    process_events()

    en_record_clean = window.cleaned_view.empty_state.action_button.text()
    en_start_launch = window.start_view.process_button.text()

    for lang in ("ru", "en", "ru"):
        i18n.set_locale(lang)
        process_events()

    i18n.set_locale("ru")
    process_events()
    assert window.cleaned_view.empty_state.action_button.text() != en_record_clean
    assert window.start_view.process_button.text() != en_start_launch
    assert window.transcript_view.copy_btn.text() == i18n.tr("btn_copy")
    assert window.status_bar.status_label.text() == i18n.tr("status_idle")

    window.close()
    process_events()


def test_settings_dialog_retranslates_in_place(process_events):
    from ui.main_window import MainWindow
    from ui.settings_dialog import SettingsDialog

    i18n.set_locale("en")
    window = MainWindow()
    dialog = SettingsDialog(window)
    process_events()

    en_apply = dialog._apply_button.text()
    en_menu = window._i18n_menu_items[0][0].title()

    # Drive the real Apply path: pick Russian and hit Apply.
    dialog._lang_ui_combo.setCurrentIndex(dialog._lang_ui_combo.findData("ru"))
    dialog._on_apply()
    process_events()

    assert i18n.current_lang() == "ru"
    assert dialog._apply_button.text() == i18n.tr("settings_apply")
    assert dialog._apply_button.text() != en_apply
    # The window behind the dialog retranslated too.
    assert window._i18n_menu_items[0][0].title() != en_menu
    # A retranslate must not re-dirty the form.
    assert not dialog._apply_button.isEnabled()

    dialog.close()
    window.close()
    process_events()


def test_subtitles_clips_and_tasks_tabs_retranslate_and_keep_their_data(process_events, monkeypatch, tmp_path):
    from domain import clips as c
    from domain import subtitles as sub
    from domain import tasks as t
    from ui.clips_view import ClipsView
    from ui.subtitle_view import SubtitleView
    from ui.tasks_view import TasksView

    i18n.set_locale("ru")
    subs, clips, tasks = SubtitleView(), ClipsView(), TasksView()
    subs._track.cues = [sub.SubtitleCue(0.0, 2.0, "typed text")]
    subs._render()
    subs.table.setCurrentCell(0, 2)
    clips.title_edit.setText("typed title")
    clips.start_edit.setText("00:00:03.000")
    clips.timeline.setCurrentIndex(clips.timeline.findData(c.EDITED))
    card = t.new_user_card("write the report", [])
    tasks.set_cards([card])
    tasks.new_task.setText("half typed")
    process_events()
    ru = (subs.generate_btn.text(), clips.export_btn.text(), tasks.add_btn.text(),
          subs.table.horizontalHeaderItem(0).text(), tasks.new_task.placeholderText(),
          clips.timeline.itemText(0), clips.status.text(), tasks._md_action.text())
    for lang in ("en", "ru", "en"):
        i18n.set_locale(lang)
        process_events()
    assert subs.generate_btn.text() == i18n.tr("subs_generate")
    assert clips.export_btn.text() == i18n.tr("clips_export")
    assert tasks.add_btn.text() == i18n.tr("task_add")
    assert subs.table.horizontalHeaderItem(0).text() == i18n.tr("subs_col_in")
    assert tasks.new_task.placeholderText() == i18n.tr("task_add_placeholder")
    assert clips.timeline.itemText(0) == i18n.tr("clips_timeline_source")
    assert clips.status.text() == i18n.tr("clips_empty")
    assert tasks._md_action.text() == i18n.tr("task_export_md")
    assert ru[0] != subs.generate_btn.text() and ru[2] != tasks.add_btn.text()
    # Input and selection survive the round trip.
    assert subs.table.item(0, 2).text() == "typed text" and subs.table.currentRow() == 0
    assert clips.title_edit.text() == "typed title" and clips.start_edit.text() == "00:00:03.000"
    assert clips.timeline.currentData() == c.EDITED
    assert tasks.new_task.text() == "half typed" and [x.task for x in tasks.cards()] == ["write the report"]
