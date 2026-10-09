"""Y1 (docs/archive/UI_CONCEPT_IMPLEMENTATION_PLAN_2026-10.ru.md): the YouTube
tab's chapters are clickable rows that move the player, listed exactly as
the timecode block will be.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QPushButton

from transcriber import Segment, TranscriptionResult
from ui.components import ChapterRow

_CHAPTERS = [
    {"start": 30, "title": "Intro"},           # becomes 0:00 for YouTube
    {"start": 34, "title": "Squeezed"},        # dropped: 4 s after Intro
    {"start": 125, "title": "Body"},
    {"start": 3725, "title": "Late part"},
]


def _panel():
    from ui.youtube_panel import YouTubePanel

    panel = YouTubePanel()
    panel.set_segments([Segment(0.0, 4000.0, "hello")], transcript_language="en")
    panel.begin_generating()
    return panel


def _rows(panel) -> list[ChapterRow]:
    return panel._chapter_rows.findChildren(ChapterRow)


def test_rows_match_the_timecode_block():
    panel = _panel()
    panel.set_result({"chapters": _CHAPTERS})
    rows = _rows(panel)
    labels = [row.findChild(QPushButton).text() for row in rows]
    assert labels == ["0:00", "2:05", "1:02:05"]
    assert panel._chapter_scroll.isVisibleTo(panel)
    assert not panel._chapters_edit.isVisibleTo(panel)
    # Copy/Save still read the text form.
    assert panel._chapters_edit.toPlainText().splitlines()[0] == "0:00 Intro"
    panel.close()


def test_clicking_a_time_asks_to_seek():
    panel = _panel()
    panel.set_result({"chapters": _CHAPTERS})
    seen = []
    panel.seek_requested.connect(seen.append)
    _rows(panel)[1].findChild(QPushButton).click()
    assert seen == [125]
    panel.close()


def test_error_shows_the_message_instead_of_rows(process_events):
    panel = _panel()
    panel.set_result({"chapters": _CHAPTERS})
    panel.set_error("LM Studio timed out")
    process_events()   # deleteLater() of the old rows
    assert _rows(panel) == []
    assert not panel._chapter_scroll.isVisibleTo(panel)
    assert panel._chapters_edit.isVisibleTo(panel)
    panel.close()


def test_new_result_replaces_the_rows(process_events):
    panel = _panel()
    panel.set_result({"chapters": _CHAPTERS})
    panel.begin_generating()
    panel.set_result({"chapters": [{"start": 0, "title": "Only"}]})
    process_events()
    assert [r.findChild(QPushButton).text() for r in _rows(panel)] == ["0:00"]
    panel.close()


def test_main_window_moves_the_player(monkeypatch):
    # Patched on the class before MainWindow exists, so the connection
    # MainWindow makes is to the recording stand-in.
    seen = []
    monkeypatch.setattr(
        "ui.player_widget.PlayerWidget.seek_to", lambda self, seconds: seen.append(seconds),
    )
    from ui.main_window import MainWindow

    window = MainWindow()
    window._document_session.apply_result(TranscriptionResult(
        segments=[Segment(0.0, 300.0, "hi")], language="en", duration=300.0,
    ))
    window.youtube_panel.begin_generating()
    window.youtube_panel.set_result({"chapters": [
        {"start": 0, "title": "A"}, {"start": 60, "title": "B"}, {"start": 120, "title": "C"},
    ]})
    _rows(window.youtube_panel)[2].findChild(QPushButton).click()
    assert seen == [120]
    window.close()


def test_questions_keep_their_own_times():
    panel = _panel()
    panel.set_result({"chapters": [], "yt_questions": [
        {"start": 165, "title": "Why offline?"},
        {"start": 170, "title": "And speed?"},
    ]})
    assert panel._questions_edit.toPlainText() == "2:45 Why offline?\n2:50 And speed?"
    panel.close()
