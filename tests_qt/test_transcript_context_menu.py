"""Transcript context menu (R4): copy with timecode, a chapter title
suggestion, and adding a YouTube chapter as a user edit."""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from transcriber import Segment, TranscriptionResult
from ui.transcript_view import TranscriptView


def _view(process_events) -> TranscriptView:
    view = TranscriptView()
    view.resize(900, 500)
    view.show()
    view._show_timestamps = False
    view.set_result(TranscriptionResult(
        segments=[Segment(0.0, 5.0, "intro words."), Segment(65.0, 70.0, "budget is the main topic today.")],
        language="en", duration=70.0,
    ))
    process_events()
    return view


def test_copy_with_time_cites_the_segment(process_events):
    view = _view(process_events)
    view._copy_with_time(view._segment_spans[1])
    assert QApplication.clipboard().text() == "[01:05] budget is the main topic today."
    view.close()


def test_chapter_suggestion_is_the_first_words_capitalised(process_events):
    view = _view(process_events)
    assert view._chapter_suggestion(view._segment_spans[1]) == "Budget is the main topic today"
    view.close()


def test_youtube_panel_adds_a_chapter_as_a_user_edit(monkeypatch, tmp_path, process_events):
    from ui.youtube_panel import YouTubePanel

    monkeypatch.setattr("core.paths.data_dir", lambda: tmp_path)
    panel = YouTubePanel()
    panel.set_provenance(None, None)
    assert not panel.can_add_chapter()
    panel.set_result({
        "chapters": [{"start": 0, "title": "Intro"}, {"start": 120, "title": "End"}],
        "yt_titles": [], "yt_description": [], "yt_tags": [], "yt_questions": [],
    })
    assert panel.can_add_chapter()
    assert panel.add_chapter(65.4, "Budget")
    assert [c["title"] for c in panel.record_chapters()] == ["Intro", "Budget", "End"]
    assert panel._overlay.get("chapters")
