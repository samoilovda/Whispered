"""Bookmarks (R3): B bookmarks the playhead in the open record; markers
on the player's timeline and underlines in the transcript follow; the
palette lists them and opens a record at one."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from core.history import HistoryStore
from transcriber import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[Segment(0.0, 10.0, "First part."), Segment(10.0, 20.0, "Second part.")],
        language="en", duration=20.0,
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


def test_b_bookmarks_the_playhead_and_shows_it(window, monkeypatch, process_events):
    record = window._test_store.add(_result(), source_path="/media/a.mp4", model="")
    window._open_record_view(record)
    process_events()
    monkeypatch.setattr(window.player, "current_position", lambda: 12.0)

    window._add_bookmark()
    process_events()

    marks = window._test_store.list_bookmarks(record)
    assert [m.at_seconds for m in marks] == [12.0]
    assert [b[0] for b in window.player._marks._bookmarks] == [12.0]
    underlined = [
        sel for sel in window.transcript_view.text_edit.extraSelections()
        if sel.format.fontUnderline()
    ]
    assert underlined and underlined[0].cursor.selectedText() == "Second part."


def test_without_a_saved_record_nothing_is_stored(window, process_events):
    window._last_record_id = None
    window._add_bookmark(5.0)
    assert window._bookmarks == []


def test_palette_lists_bookmarks_and_opens_one(window, monkeypatch, process_events):
    first = window._test_store.add(_result(), source_path="/media/a.mp4", model="")
    other = window._test_store.add(_result(), source_path="/media/b.mp4", model="")
    window._test_store.add_bookmark(other, 15.0, "budget quote")
    window._open_record_view(first)
    process_events()

    seeks = []
    monkeypatch.setattr(window.player, "seek_to", seeks.append)
    palette = window.command_palette
    palette._refresh("budget")
    payloads = [
        palette.results.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(palette.results.count())
    ]
    assert ("bookmark", (other, 15.0)) in payloads
    palette.bookmark_requested.emit(other, 15.0)
    process_events()
    assert window._last_record_id == other
    assert seeks == [15.0]
