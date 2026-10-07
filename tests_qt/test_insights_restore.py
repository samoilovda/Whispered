"""Opening a record from the Library shows that record's own Insights, and
never the previously open record's (the same fix the YouTube tab got).
"""

from __future__ import annotations

import json

import pytest
from PyQt6.QtWidgets import QLabel

from core.history import HistoryStore
from transcriber import Segment, TranscriptionResult
from ui.components import ChapterRow

_INSIGHTS = {
    "chapters": [{"start": 0, "title": "Saved intro"}, {"start": 90, "title": "Saved body"}],
    "action_items": [{"task": "Saved task", "owner": None, "deadline": None}],
    "key_moments": [],
}


def _result(text: str) -> TranscriptionResult:
    return TranscriptionResult(segments=[Segment(0.0, 300.0, text)], language="en", duration=300.0)


@pytest.fixture
def window(monkeypatch, tmp_path):
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    from ui.main_window import MainWindow

    win = MainWindow()
    win._test_store = store
    yield win
    win.close()


def _save(record_id: int, source: str, name: str, payload: dict) -> None:
    from core.paths import artifact_dir

    folder = artifact_dir(record_id, source or "recording")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(payload), encoding="utf-8")


def _chapter_titles(win) -> list[str]:
    rows = win.insights_panel._ch_container.findChildren(ChapterRow)
    return [row.findChildren(QLabel)[0].text() for row in rows]


def test_opening_a_record_restores_its_insights(window):
    record = window._test_store.add(_result("a"), source_path="/media/a.mp4", model="")
    _save(record, "/media/a.mp4", "insights.json", _INSIGHTS)

    window._open_record_view(record)
    panel = window.insights_panel
    assert _chapter_titles(window) == ["Saved intro", "Saved body"]
    assert panel.own_chapters() == _INSIGHTS["chapters"]
    assert panel._save_btn.isEnabled()
    assert [t for _, t in window.player._chapter_marks] == ["Saved intro", "Saved body"]


def test_previous_records_insights_do_not_leak_into_the_next(window):
    first = window._test_store.add(_result("a"), source_path="/media/a.mp4", model="")
    second = window._test_store.add(_result("b"), source_path="/media/b.mp4", model="")
    _save(first, "/media/a.mp4", "insights.json", _INSIGHTS)

    window._open_record_view(first)
    assert _chapter_titles(window)

    window._open_record_view(second)
    panel = window.insights_panel
    assert _chapter_titles(window) == []
    assert panel.own_chapters() == []
    assert not panel._save_btn.isEnabled()
    assert window.player._chapter_marks == []


def test_youtube_chapters_still_win_after_restoring_both(window):
    record = window._test_store.add(_result("a"), source_path="/media/a.mp4", model="")
    _save(record, "/media/a.mp4", "insights.json", _INSIGHTS)
    _save(record, "/media/a.mp4", "youtube_package.json", {
        "chapters": [{"start": 0, "title": "YT intro"}, {"start": 60, "title": "YT body"}],
        "yt_titles": ["T"], "yt_description": ["H"], "yt_tags": [], "yt_questions": [],
    })
    window._open_record_view(record)
    assert _chapter_titles(window) == ["YT intro", "YT body"]
    assert window.insights_panel.own_chapters() == _INSIGHTS["chapters"]


def test_a_broken_insights_file_leaves_the_tab_empty(window):
    from core.paths import artifact_dir

    record = window._test_store.add(_result("c"), source_path="/media/c.mp4", model="")
    folder = artifact_dir(record, "/media/c.mp4")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "insights.json").write_text("{not json", encoding="utf-8")
    assert window._load_from_history(record)
    assert _chapter_titles(window) == []
