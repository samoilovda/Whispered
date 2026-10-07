"""The record screen's header and tab row: material tabs appear when they
have something to show (or are asked for from "+"), the title renames
the record in place, and the last-run chip summarises the run."""

from __future__ import annotations

import pytest

from core.history import HistoryStore
from transcriber import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello world")], language="en", duration=1.0,
    )


@pytest.fixture
def window(monkeypatch, tmp_path, process_events):
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    from ui.main_window import MainWindow

    win = MainWindow()
    win.show()
    win._test_store = store
    process_events()
    yield win
    win.close()
    process_events()


def _visible_tabs(win) -> list:
    tabs = win.main_tabs
    return [tabs.widget(i) for i in range(tabs.count()) if tabs.isTabVisible(i)]


def test_material_tabs_wait_for_content(window, process_events):
    record = window._test_store.add(_result(), source_path="/media/a.mp4", model="")
    window._open_record_view(record)
    process_events()

    visible = _visible_tabs(window)
    assert window.transcript_view in visible
    assert window.cut_view in visible and window.chat_panel in visible
    for hidden in (window.cleaned_view, window.article_view, window.youtube_panel,
                   window.insights_panel, window.book_panel):
        assert hidden not in visible
    labels = [a.text() for a in window._add_tab_menu.actions()]
    assert len(labels) == 5

    # Content makes a tab appear on its own.
    window.cleaned_view.set_text("clean words")
    process_events()
    assert window.cleaned_view in _visible_tabs(window)


def test_plus_menu_reveals_a_tab_with_its_create_action(window, process_events):
    record = window._test_store.add(_result(), source_path="/media/a.mp4", model="")
    window._open_record_view(record)
    process_events()

    window._show_tab(window.article_view)
    process_events()
    assert window.main_tabs.currentWidget() is window.article_view
    assert window.article_view.empty_state.isVisibleTo(window.article_view)

    # Opening another record forgets what was revealed.
    other = window._test_store.add(_result(), source_path="/media/b.mp4", model="")
    window._open_record_view(other)
    process_events()
    assert window.article_view not in _visible_tabs(window)


def test_renaming_from_the_header_updates_the_library(window, process_events):
    record = window._test_store.add(_result(), source_path="/media/a.mp4", model="")
    window._open_record_view(record)
    process_events()
    assert window.record_view.title() == "a"

    window.record_view.rename_requested.emit("Team sync")
    process_events()
    assert window._test_store.get_title(record) == "Team sync"


def test_run_chip_summarises_the_last_run(window, process_events):
    from application.job_engine import JobRun
    from application.run_store import save_run
    from domain.job import JobSpec, StepOutcome, StepSpec, StepStatus

    record = window._test_store.add(_result(), source_path="/media/a.mp4", model="")
    run = JobRun(spec=JobSpec(name="youtube_video", steps=(StepSpec("transcribe"), StepSpec("insights"))))
    run.outcomes["transcribe"] = StepOutcome("transcribe", StepStatus.SUCCEEDED)
    run.outcomes["insights"] = StepOutcome("insights", StepStatus.FAILED, error="timed out")
    save_run(record, "youtube_video", run, status="failed")

    window._open_record_view(record)
    process_events()
    chip = window.record_view.run_chip
    assert chip.isVisibleTo(window.record_view)
    assert "✓ 1" in chip.text() and "✕ 1" in chip.text()
    assert "timed out" in chip.toolTip()
    assert chip.isEnabled()  # a failed run can be resumed from here
