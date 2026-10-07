"""File > Create YouTube package (and its Ctrl+K row): the one way to run
the "youtube_package" step alone for the open record — see
docs/UI_CONCEPT_IMPLEMENTATION_PLAN_2026-10.ru.md, Y2a.
"""

from __future__ import annotations

from transcriber import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello there")],
        language="en",
        duration=1.0,
    )


_PAYLOAD = {
    "chapters": [{"start": 0, "title": "Intro"}],
    "yt_titles": ["A Great Title"],
    "yt_description": ["A hook.\n\nA summary."],
    "yt_tags": ["podcast"],
    "yt_questions": [],
}


def _action(window, key: str):
    for obj, label_key, _setter in window._i18n_menu_items:
        if label_key == key:
            return obj
    raise AssertionError(f"no menu action {key!r}")


def test_action_is_in_the_command_palette():
    from ui.main_window import MainWindow

    window = MainWindow()
    action = _action(window, "menu_run_youtube_package")
    assert action in window.command_palette._actions
    window.close()


def test_action_runs_the_step_and_shows_the_youtube_tab(monkeypatch, process_events):
    monkeypatch.setattr(
        "core.insights.generate_insight", lambda insight_type, segments, **kw: _PAYLOAD[insight_type],
    )
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    get_config().lm_studio_url = "http://127.0.0.1:1234"
    get_config().yt_provider = "lmstudio"
    window._document_session.apply_result(_result())
    window._last_record_id = None
    window._source_filepath = None

    _action(window, "menu_run_youtube_package").trigger()
    runner = window._youtube_job
    assert runner is not None
    assert window.main_tabs.currentWidget() is window.youtube_panel

    # A second trigger while the first runs must not replace the runner.
    _action(window, "menu_run_youtube_package").trigger()
    assert window._youtube_job is runner

    assert runner.wait(2000)
    process_events()
    assert window.youtube_panel._chapters_data == _PAYLOAD["chapters"]
    window.close()


def test_action_without_a_record_starts_nothing(process_events):
    from ui.main_window import MainWindow

    window = MainWindow()
    _action(window, "menu_run_youtube_package").trigger()
    process_events()
    assert window._youtube_job is None
    window.close()
