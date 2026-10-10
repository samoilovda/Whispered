"""A recipe run belongs to the record it started on: opening another
record while it runs must not put the run's results in that record's
tabs, nor save the run or its badges under that record."""

from __future__ import annotations

import threading

import pytest

from transcriber import Segment, TranscriptionResult


def _result(text: str) -> TranscriptionResult:
    return TranscriptionResult(segments=[Segment(0.0, 1.0, text)], language="en", duration=1.0)


@pytest.fixture
def window(monkeypatch, tmp_path):
    import config
    import core.history as history

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config(last_recipe="meeting_notes"))
    store = history.HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr(history, "_store", store)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    from ui.main_window import MainWindow

    win = MainWindow()
    win._test_store = store
    yield win
    win.close()


def test_results_of_a_run_stay_with_its_record(window, monkeypatch, process_events):
    from application.run_store import load_latest_run

    started = threading.Event()
    release = threading.Event()

    def _slow_insight(insight_type, segments, **_kw):
        started.set()
        release.wait(5)
        return [{"start": 0, "title": "From A"}] if insight_type == "chapters" else []

    monkeypatch.setattr("core.insights.generate_insight", _slow_insight)
    store = window._test_store
    a = store.add(_result("alpha"), source_path="/media/a.mp4", model="")
    b = store.add(_result("beta"), source_path="/media/b.mp4", model="")

    window._open_record_view(a)
    window.recipe_run.start(window._current_result, show_run_screen=False)
    assert started.wait(3)
    assert window.recipe_run.record_id == a

    window._open_record_view(b)          # the user moves on mid-run
    process_events()
    release.set()
    assert window.recipe_run.job is None or window.recipe_run.job.wait(5000)
    process_events()

    assert not window.insights_panel._results       # B's tab untouched
    assert load_latest_run(a) is not None
    assert load_latest_run(b) is None
    assert "insights" in (store.get_record(a)["artifacts"] or [])
