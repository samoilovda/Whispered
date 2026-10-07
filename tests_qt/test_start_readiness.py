"""Start screen (S1): the selected recipe's step chain and readiness
line, fed by the status bar's LM Studio probe; Launch explains itself."""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QPushButton

from core.i18n import load_locale, tr


def _ready_labels(view) -> dict:
    return {
        label.property("readiness"): label
        for label in view._ready_widget.findChildren(QLabel)
        if label.property("readiness") and not label.isHidden()
    }


def test_readiness_follows_the_recipe_and_the_probe(process_events):
    load_locale("en")
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()
    view = window.start_view

    view.set_recipe("transcript_only")
    process_events()
    assert "llm" not in _ready_labels(view)
    assert view._steps_label.text() == tr("step_transcribe")

    view.set_recipe("meeting_notes")
    view.set_llm_status(False)
    process_events()
    llm = _ready_labels(view)["llm"]
    assert llm.text().startswith("✕")
    assert llm.property("role") == "danger-text"

    fixes = []
    view.fix_requested.disconnect()  # the window's handler opens a modal dialog
    view.fix_requested.connect(fixes.append)
    link = next(
        b for b in view._ready_widget.findChildren(QPushButton)
        if b.text() == tr("ready_fix_settings_ai")
    )
    link.click()
    assert fixes == ["settings_ai"]

    view.set_llm_status(True)
    process_events()
    assert _ready_labels(view)["llm"].text().startswith("✓")

    window.close()
    process_events()


def test_disabled_launch_says_why(process_events):
    load_locale("en")
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()
    view = window.start_view
    view.set_source("file")
    view.set_process_enabled(False)
    process_events()
    assert view._launch_hint.isVisible()
    assert view._launch_hint.text() == tr("start_hint_pick_file")
    view.set_process_enabled(True)
    process_events()
    assert not view._launch_hint.isVisible()

    window.close()
    process_events()
