"""Opening a record from the Library shows that record's own YouTube
package (with the user's edits), and never the previously open record's.
"""

from __future__ import annotations

import json

import pytest

from core.history import HistoryStore
from transcriber import Segment, TranscriptionResult

_PACKAGE = {
    "chapters": [
        {"start": 0, "title": "Intro"}, {"start": 60, "title": "Body"}, {"start": 120, "title": "End"},
    ],
    "yt_titles": ["Saved title", "Other"],
    "yt_description": ["Saved hook."],
    "yt_tags": ["saved"],
    "yt_questions": [],
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


def _save_package(record_id: int, source: str) -> None:
    from core.paths import artifact_dir

    folder = artifact_dir(record_id, source or "recording")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "youtube_package.json").write_text(json.dumps(_PACKAGE), encoding="utf-8")


def test_opening_a_record_restores_its_package(window):
    record = window._test_store.add(_result("a"), source_path="/media/a.mp4", model="")
    _save_package(record, "/media/a.mp4")

    window._open_record_view(record)
    panel = window.youtube_panel
    assert panel._state == "done"
    assert panel._chapters_edit.toPlainText().startswith("0:00 Intro")
    assert panel.publish_texts()["titles"][0] == "Saved title"
    assert "Saved hook." in panel._desc_edit.toPlainText()
    # The sections themselves are on screen, not just the status line.
    assert not panel._tabs.isHidden()


def test_record_without_a_package_offers_to_create_one(window):
    record = window._test_store.add(_result("b"), source_path="/media/b.mp4", model="")
    window._open_record_view(record)
    assert window.youtube_panel._state == "ready"
    assert not window.youtube_panel._run_link.isHidden()
    assert window.youtube_panel._chapters_data is None


def test_previous_records_package_does_not_leak_into_the_next(window):
    first = window._test_store.add(_result("a"), source_path="/media/a.mp4", model="")
    second = window._test_store.add(_result("b"), source_path="/media/b.mp4", model="")
    _save_package(first, "/media/a.mp4")

    window._open_record_view(first)
    assert window.youtube_panel._state == "done"

    window._open_record_view(second)
    panel = window.youtube_panel
    assert panel._state == "ready"
    assert panel._chapters_edit.toPlainText() == ""
    assert panel.publish_texts()["titles"] == []
    assert not panel.has_publishable_content()


def test_edits_come_back_with_the_record(window):
    first = window._test_store.add(_result("a"), source_path="/media/a.mp4", model="")
    second = window._test_store.add(_result("b"), source_path="/media/b.mp4", model="")
    _save_package(first, "/media/a.mp4")
    _save_package(second, "/media/b.mp4")

    window._open_record_view(first)
    panel = window.youtube_panel
    panel._chapters_edit_btn.click()
    panel._chapters_edit.setPlainText("0:00 Edited intro\n1:00 Body\n2:00 End")
    panel._chapters_done_btn.click()
    panel._offset_spin.setValue(10)

    window._open_record_view(second)
    assert panel._chapters_edit.toPlainText().startswith("0:00 Intro")   # not the edit
    assert panel._offset_spin.value() == 0

    window._open_record_view(first)
    assert panel._chapters_edit.toPlainText().startswith("0:00 Edited intro")
    assert panel._offset_spin.value() == 10
    assert "1:10 Body" in panel._chapters_edit.toPlainText()


def test_a_broken_package_file_leaves_the_tab_empty(window):
    from core.paths import artifact_dir

    record = window._test_store.add(_result("c"), source_path="/media/c.mp4", model="")
    folder = artifact_dir(record, "/media/c.mp4")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "youtube_package.json").write_text("{not json", encoding="utf-8")

    assert window._load_from_history(record)
    assert window.youtube_panel._state == "ready"


def test_cleaned_text_and_articles_follow_the_opened_record(window, process_events):
    """Record B never shows record A's cleaned text or articles — and A's
    cleaned text no longer feeds B's article generation — while each
    record's own saved ones come back on opening."""
    from core.paths import artifact_dir

    a = window._test_store.add(_result("alpha"), source_path="/media/a.mp4", model="")
    b = window._test_store.add(_result("beta"), source_path="/media/b.mp4", model="")
    folder = artifact_dir(a, "/media/a.mp4")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "clean.md").write_text("Alpha, cleaned.", encoding="utf-8")
    (folder / "articles.json").write_text(
        json.dumps({"summary": {"title": "A summary", "content": "About alpha."}}),
        encoding="utf-8",
    )

    window._open_record_view(a)
    process_events()
    assert window.cleaned_view.get_text() == "Alpha, cleaned."
    assert window.article_view.has_articles()
    assert window._get_text_for_ai() == "Alpha, cleaned."

    window._open_record_view(b)
    process_events()
    assert window.cleaned_view.get_text() == ""
    assert not window.article_view.has_articles()
    assert window._get_text_for_ai() == "beta"


def test_a_record_with_moved_media_keeps_one_output_folder(window, process_events):
    """Its media gone, a record's output folder is still the one named
    after its stored source — where Clean/Articles write and where
    reopening looks — not a separate "recording-<id>" folder."""
    from core.paths import artifact_dir

    record = window._test_store.add(_result("gamma"), source_path="/gone/talk.mp4", model="")
    window._open_record_view(record)
    process_events()
    assert window._source_filepath is None  # the media isn't there
    assert artifact_dir(record, window._artifact_source()) == artifact_dir(record, "/gone/talk.mp4")
