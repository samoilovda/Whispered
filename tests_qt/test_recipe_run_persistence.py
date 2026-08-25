"""Real-Qt test for MainWindow's B8 wiring of a recipe launch into
application/run_store.py's job_runs table (see
docs/UI_REDESIGN_PLAN_2026-09.ru.md, B8) — the Library card's run
composition and the command palette's retriable steps both read this.
"""

from __future__ import annotations

from application.run_store import load_latest_run
from config import get_config
from core.history import HistoryStore
from transcriber import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello world")], language="en", duration=1.0,
    )


def test_a_finished_recipe_run_is_persisted_to_job_runs(monkeypatch, tmp_path, process_events):
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    get_config().last_recipe = "transcript_only"

    from ui.main_window import MainWindow

    window = MainWindow()
    window._current_result = _result()
    window._last_record_id = store.add(_result(), source_path="", model="")

    window._run_recipe(_result())
    runner = window._recipe_job
    assert runner is not None
    assert runner.wait(2000)
    process_events()

    stored = load_latest_run(window._last_record_id)
    assert stored is not None
    assert stored.recipe == "transcript_only"
    assert stored.status == "done"
    assert stored.outcomes["transcribe"]["status"] == "succeeded"

    window.close()
