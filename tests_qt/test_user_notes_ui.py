"""L1: notes typed during a live session are saved with the record; the
Insights tab shows and autosaves a record's notes."""

from __future__ import annotations

import pytest

from core.history import HistoryStore
from transcriber import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(segments=[Segment(0.0, 5.0, "hello")], language="en", duration=5.0)


@pytest.fixture
def window(monkeypatch, tmp_path, process_events):
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    from ui.main_window import MainWindow

    win = MainWindow()
    win._test_store = store
    yield win
    win.close()
    process_events()


def test_ctrl_enter_inserts_the_session_time(window, process_events):
    live = window.live_view
    live._elapsed_seconds = 754.0  # what set_metrics() records each tick
    live.notes_edit.setPlainText("first thought")
    live.notes_edit.moveCursor(live.notes_edit.textCursor().MoveOperation.End)
    live.insert_time_mark()
    assert live.notes_text() == "first thought\n[12:34] "


def test_live_notes_become_the_records_notes(window, process_events):
    from application.user_notes import load_notes
    from core.paths import artifact_dir

    record = window._test_store.add(_result(), source_path="", model="", source_kind="live")
    window._last_record_id = record
    window._source_filepath = None
    window.live_view.notes_edit.setPlainText("[00:42] ask about pricing")
    window._save_live_notes()
    assert load_notes(artifact_dir(record, "recording")) == "[00:42] ask about pricing"
    assert window.live_view.notes_text() == ""
    hits = window._test_store.search_artifacts("pricing")
    assert [(h.record_id, h.type) for h in hits] == [(record, "notes")]


def test_insights_tab_edits_and_saves_notes(window, process_events):
    from application.user_notes import load_notes
    from core.paths import artifact_dir

    record = window._test_store.add(_result(), source_path="/media/a.mp4", model="")
    window._open_record_view(record)
    process_events()
    panel = window.insights_panel
    assert panel._notes_edit.isEnabled()
    panel._notes_edit.setPlainText("check the numbers")
    panel._save_notes()
    assert load_notes(artifact_dir(record, "/media/a.mp4")) == "check the numbers"
    process_events()
    # Notes count as content: the Insights tab shows up for the record.
    assert window.main_tabs.isTabVisible(window.main_tabs.indexOf(panel))


def test_a_running_session_gives_the_screen_to_transcript_and_notes(window, process_events):
    window.show()
    window._on_section_changed("live")
    process_events()
    window.live_runtime.session_state_changed.emit("running")
    process_events()
    assert not window.live_view.setup.isVisible()
    assert not window.start_view._recipe_card.isVisible()
    assert window.live_view.stop_btn.isVisible()
    window.live_runtime.session_state_changed.emit("completed")
    process_events()
    assert window.live_view.setup.isVisible()
    assert window.start_view._recipe_card.isVisible()
