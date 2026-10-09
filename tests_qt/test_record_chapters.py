"""Y6 (docs/archive/UI_CONCEPT_IMPLEMENTATION_PLAN_2026-10.ru.md): one chapter
list per record — the YouTube tab's (with the user's edits) wins over the
Insights step's own, in recording time.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QLabel, QPushButton

from transcriber import Segment, TranscriptionResult
from ui.components import ChapterRow

_YT = {
    "chapters": [
        {"start": 0, "title": "YT intro"}, {"start": 60, "title": "YT body"}, {"start": 120, "title": "YT end"},
    ],
    "yt_titles": ["T"], "yt_description": ["Hook."], "yt_tags": ["a"], "yt_questions": [],
}
_INSIGHTS = {
    "chapters": [{"start": 0, "title": "Ins intro"}, {"start": 90, "title": "Ins body"}],
    "action_items": [], "key_moments": [],
}


@pytest.fixture
def window():
    from ui.main_window import MainWindow

    win = MainWindow()
    win._document_session.apply_result(TranscriptionResult(
        segments=[Segment(0.0, 300.0, "hi")], language="en", duration=300.0,
    ))
    win.youtube_panel.set_provenance(41, "/media/talk.mp4")
    yield win
    win.close()


def _insights_titles(win) -> list[str]:
    rows = win.insights_panel._ch_container.findChildren(ChapterRow)
    return [row.findChildren(QLabel)[0].text() for row in rows]


def test_insights_shows_its_own_chapters_without_a_youtube_package(window):
    window.insights_panel.set_result(_INSIGHTS)
    assert _insights_titles(window) == ["Ins intro", "Ins body"]
    assert not window.insights_panel._ch_note.isVisibleTo(window.insights_panel)


def test_youtube_chapters_win_and_edits_follow(window):
    window.insights_panel.set_result(_INSIGHTS)
    panel = window.youtube_panel
    panel.begin_generating()
    panel.set_result(_YT)
    assert _insights_titles(window) == ["YT intro", "YT body", "YT end"]
    assert not window.insights_panel._ch_note.isHidden()

    panel._chapters_edit_btn.click()
    panel._chapters_edit.setPlainText("0:00 Edited intro\n1:00 YT body\n2:00 YT end")
    panel._chapters_done_btn.click()
    assert _insights_titles(window)[0] == "Edited intro"

    panel._chapters_reset_btn.click()
    assert _insights_titles(window)[0] == "YT intro"


def test_record_chapters_are_in_recording_time(window):
    panel = window.youtube_panel
    panel.begin_generating()
    panel.set_result(_YT)
    panel._offset_spin.setValue(15)
    assert [c["start"] for c in panel.record_chapters()] == [0, 60, 120]
    rows = window.insights_panel._ch_container.findChildren(ChapterRow)
    assert [r.findChild(QPushButton).text() for r in rows] == ["00:00", "01:00", "02:00"]


def test_without_a_package_insights_goes_back_to_its_own(window):
    window.insights_panel.set_result(_INSIGHTS)
    panel = window.youtube_panel
    panel.begin_generating()
    panel.set_result(_YT)
    panel.begin_generating()            # package being regenerated: none shown
    assert _insights_titles(window) == ["Ins intro", "Ins body"]
    panel.set_error("LM Studio timed out")
    assert _insights_titles(window) == ["Ins intro", "Ins body"]


def test_insights_save_writes_the_chapters_on_screen(window):
    from core.paths import output_dir

    window.insights_panel.set_result(_INSIGHTS)
    window.insights_panel.set_source_name("talk")
    panel = window.youtube_panel
    panel.begin_generating()
    panel.set_result(_YT)
    window.insights_panel._save_to_files()
    saved = (output_dir() / "talk_chapters.txt").read_text(encoding="utf-8")
    assert "YT intro" in saved and "Ins intro" not in saved
