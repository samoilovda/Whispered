"""Real-Qt test for YouTubePanel's "Generate" button (generate_requested
-> MainWindow._start_youtube_job), now routed through
application/steps.py's "youtube_package" step via JobRunner instead of
five independent InsightsWorker instances (see
docs/UI_REDESIGN_PLAN_2026-09.ru.md, B5d — the same migration B5a/B5b/B5c
did for clean/article/insights, and the largest of the six).
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
    "yt_titles": ["A Great Title"],
    "yt_description": ["A hook.\n\nA summary."],
    "yt_tags": ["podcast", "interview"],
    "yt_questions": [{"start": 5, "title": "What now?"}],
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

    window.youtube_panel.generate_requested.emit()
    runner = window._youtube_job
    assert runner is not None

    assert runner.wait(2000)
    process_events()

    assert window._youtube_job is None
    assert window.youtube_panel._chapters_data == _PAYLOAD["chapters"]
    assert window.youtube_panel._titles_edit.toPlainText() == "1. A Great Title"
    assert window.youtube_panel._copy_btn.isEnabled()
    assert window.youtube_panel._save_btn.isEnabled()

    from core.paths import artifact_dir
    manifest = artifact_dir("unsaved", "recording") / "youtube_package.json.manifest.json"
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
    get_config().yt_provider = "lmstudio"
    window._current_result = _result()

    window.youtube_panel.generate_requested.emit()
    process_events()

    assert window._youtube_job is None
    assert window.youtube_panel._retry_label.text()

    window.close()


def test_youtube_job_failure_reports_the_error_without_crashing(
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

    window.youtube_panel.generate_requested.emit()
    runner = window._youtube_job
    assert runner is not None
    assert runner.wait(2000)
    process_events()

    assert window._youtube_job is None
    assert not window.youtube_panel._copy_btn.isEnabled()
    assert "LM Studio unreachable" in window.youtube_panel._chapters_edit.toPlainText()
    assert window.youtube_panel._retry_label.text()

    window.close()


def test_cancel_youtube_job_stops_the_worker_without_reporting_a_result(
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

    window.youtube_panel.generate_requested.emit()
    runner = window._youtube_job
    assert runner is not None
    assert started.wait(2)

    window._cancel_youtube_job()
    assert window._youtube_job is None
    release.set()
    assert runner.wait(2000)
    process_events()

    assert not window.youtube_panel._copy_btn.isEnabled()

    window.close()


def test_finished_youtube_job_takes_the_cancel_button_back_down(
    monkeypatch, process_events,
):
    """Same regression as the insights job: _start_youtube_job() showed
    the status bar's Cancel button and the finish handler never hid it."""
    monkeypatch.setattr("core.insights.generate_insight", _fake_generate_insight)
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    get_config().lm_studio_url = "http://127.0.0.1:1234"
    window._current_result = _result()
    window._last_record_id = None
    window._source_filepath = None
    process_events()
    assert not window.cancel_btn.isVisible()

    window.youtube_panel.generate_requested.emit()
    assert window._youtube_job is not None
    assert window._youtube_job.wait(5000)
    process_events()

    assert not window.cancel_btn.isVisible()

    window.close()
