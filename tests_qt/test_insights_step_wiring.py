"""Real-Qt test for InsightsPanel's "Generate" button (generate_requested
-> MainWindow._start_insights_job), now routed through
application/steps.py's "insights" step via JobRunner instead of three
independent InsightsWorker instances (see
docs/UI_REDESIGN_PLAN_2026-09.ru.md, B5c — the same migration B5a/B5b
did for clean/article).
"""

from __future__ import annotations

from transcriber import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[Segment(0.0, 1.0, "um so anyway hello there")],
        language="en",
        duration=1.0,
    )


_PAYLOAD = {
    "chapters": [{"start": 0, "title": "Intro"}],
    "action_items": [{"task": "Follow up", "owner": "Alice"}],
    "key_moments": [{"start": 5, "quote": "Wow.", "note": "reaction"}],
}


def _fake_generate_insight(insight_type, segments, **kwargs):
    return _PAYLOAD[insight_type]


def test_generate_button_runs_through_job_runner_and_writes_provenance(
    monkeypatch, process_events,
):
    monkeypatch.setattr("core.insights.generate_insight", _fake_generate_insight)
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    get_config().lm_studio_url = "http://127.0.0.1:1234"
    window._current_result = _result()
    window._last_record_id = None
    window._source_filepath = None

    window.insights_panel.generate_requested.emit()
    runner = window._insights_job
    assert runner is not None

    assert runner.wait(2000)
    process_events()

    assert window._insights_job is None
    assert window.insights_panel._results == _PAYLOAD

    from core.paths import artifact_dir
    manifest = artifact_dir("unsaved", "recording") / "insights.json.manifest.json"
    assert manifest.exists()
    assert manifest.read_text(encoding="utf-8").strip()

    window.close()


def test_no_lm_studio_url_reports_the_error_without_starting_a_job(
    monkeypatch, process_events,
):
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    get_config().lm_studio_url = ""
    window._current_result = _result()

    window.insights_panel.generate_requested.emit()
    process_events()

    assert window._insights_job is None

    window.close()


def test_insights_job_failure_reports_the_error_without_crashing(
    monkeypatch, process_events,
):
    def _failing(insight_type, segments, **kwargs):
        raise RuntimeError("LM Studio unreachable")

    monkeypatch.setattr("core.insights.generate_insight", _failing)
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    get_config().lm_studio_url = "http://127.0.0.1:1234"
    window._current_result = _result()
    window._last_record_id = None
    window._source_filepath = None

    window.insights_panel.generate_requested.emit()
    runner = window._insights_job
    assert runner is not None
    assert runner.wait(2000)
    process_events()

    assert window._insights_job is None
    assert window.insights_panel._results == {}
    assert "LM Studio unreachable" in window.insights_panel._placeholder.text()

    window.close()


def test_cancel_insights_job_stops_the_worker_without_reporting_a_result(
    monkeypatch, process_events,
):
    import threading

    release = threading.Event()
    started = threading.Event()

    def _blocking(insight_type, segments, *, is_cancelled=None, **kwargs):
        started.set()
        release.wait(5)
        if is_cancelled is not None and is_cancelled():
            return []
        return _PAYLOAD[insight_type]

    monkeypatch.setattr("core.insights.generate_insight", _blocking)
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    get_config().lm_studio_url = "http://127.0.0.1:1234"
    window._current_result = _result()
    window._last_record_id = None
    window._source_filepath = None

    window.insights_panel.generate_requested.emit()
    runner = window._insights_job
    assert runner is not None
    assert started.wait(2)

    window._cancel_insights_job()
    assert window._insights_job is None
    release.set()
    assert runner.wait(2000)
    process_events()

    assert window.insights_panel._results == {}

    window.close()
