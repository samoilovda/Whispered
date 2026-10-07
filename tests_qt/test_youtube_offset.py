"""Y5 (docs/UI_CONCEPT_IMPLEMENTATION_PLAN_2026-10.ru.md): shift the YouTube
timecodes for an intro the recording lacks, without moving edits or seeks
out of recording time.
"""

from __future__ import annotations

import json

import pytest
from PyQt6.QtWidgets import QPushButton

from transcriber import Segment
from ui.components import ChapterRow

_PAYLOAD = {
    "chapters": [
        {"start": 0, "title": "Intro"}, {"start": 60, "title": "Body"}, {"start": 120, "title": "End"},
    ],
    "yt_titles": ["T"],
    "yt_description": ["Hook."],
    "yt_tags": ["a"],
    "yt_questions": [{"start": 65, "title": "Why?"}],
}


@pytest.fixture(autouse=True)
def _no_signature():
    from config import get_config

    cfg = get_config()
    original = cfg.yt_channel_signature
    cfg.yt_channel_signature = ""
    yield
    cfg.yt_channel_signature = original


def _panel(duration: float = 600.0):
    from ui.youtube_panel import YouTubePanel

    panel = YouTubePanel()
    panel.set_provenance(31, "/media/talk.mp4")
    panel.set_segments([Segment(0.0, duration, "hello")], transcript_language="en")
    panel.begin_generating()
    panel.set_result(_PAYLOAD)
    return panel


def _labels(panel) -> list[str]:
    return [row.findChild(QPushButton).text() for row in panel._chapter_rows.findChildren(ChapterRow)]


def test_offset_shifts_timecodes_rows_and_description_but_not_the_first():
    panel = _panel()
    panel._offset_spin.setValue(15)
    assert panel._chapters_edit.toPlainText() == "0:00 Intro\n1:15 Body\n2:15 End"
    assert _labels(panel) == ["0:00", "1:15", "2:15"]
    assert "1:15 Body" in panel.publish_texts()["description"]
    assert [c[0] for c in panel.publish_texts()["chapter_check"].chapters] == [0, 75, 135]
    panel.close()


def test_questions_in_the_description_shift_too():
    panel = _panel()
    panel._desc_block_btns["questions"].click()
    panel._offset_spin.setValue(15)
    assert "1:20 Why?" in panel._desc_edit.toPlainText()
    panel.close()


def test_rows_still_seek_in_recording_time():
    panel = _panel()
    panel._offset_spin.setValue(15)
    seen = []
    panel.seek_requested.connect(seen.append)
    panel._chapter_rows.findChildren(ChapterRow)[1].findChild(QPushButton).click()
    assert seen == [60]
    panel.close()


def test_editing_stays_in_recording_time_and_says_so():
    panel = _panel()
    panel._offset_spin.setValue(15)
    panel._chapters_edit_btn.click()
    assert panel._chapters_edit.toPlainText() == "0:00 Intro\n1:00 Body\n2:00 End"
    assert "+0:15" in panel._chapters_note.text()
    assert not panel._offset_bar.isVisibleTo(panel)
    panel._chapters_edit.setPlainText("0:00 Intro\n1:30 Body\n2:00 End")
    panel._chapters_done_btn.click()
    assert panel._chapters_edit.toPlainText() == "0:00 Intro\n1:45 Body\n2:15 End"
    panel.close()


def test_offset_is_remembered_per_record_and_zero_is_no_edit():
    panel = _panel()
    panel._offset_spin.setValue(20)
    saved = json.loads(panel._overlay_file().read_text(encoding="utf-8"))
    assert saved["offset"] == 20

    again = _panel()
    assert again._offset_spin.value() == 20
    again._offset_spin.setValue(0)
    assert not again._overlay_file().exists()
    panel.close()
    again.close()


def test_checks_use_video_time():
    # Recording ends at 2:10; with a 15 s intro the video ends at 2:25, so
    # "End" (2:00 → 2:15 in the video) is not past the end.
    panel = _panel(duration=130.0)
    assert panel._chapter_check.issues_of("past_end") == ()
    panel._offset_spin.setValue(-20)
    # Negative offset: video ends at 1:50, "End" (2:00 → 1:40) still inside.
    assert panel._chapter_check.issues_of("past_end") == ()
    panel.close()


def test_negative_offset_clamps_at_zero():
    panel = _panel()
    panel._offset_spin.setValue(-90)
    # Intro and Body both land at 0:00 → Body is dropped as a duplicate.
    assert panel._chapters_edit.toPlainText() == "0:00 Intro\n0:30 End"
    panel.close()


def test_offset_bar_hidden_until_there_are_chapters():
    from ui.youtube_panel import YouTubePanel

    panel = YouTubePanel()
    assert not panel._offset_bar.isVisibleTo(panel)
    panel.close()


def test_the_intro_folding_into_the_first_chapter_is_not_a_warning():
    # 200 s keeps every chapter under 7:30, so nothing else warns.
    panel = _panel(duration=200.0)
    panel._offset_spin.setValue(15)
    assert "0:15" not in panel._chapter_status.text()
    assert panel._chapter_status.property("role") == "success-text"
    panel.close()
