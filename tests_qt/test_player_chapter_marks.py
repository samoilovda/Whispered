"""Y6: chapter ticks on the player's timeline — the record's chapters (the
YouTube tab's with edits, else the Insights step's), hover names them,
click seeks.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest

from transcriber import Segment, TranscriptionResult
from ui.player_widget import multimedia_available

pytestmark = pytest.mark.skipif(not multimedia_available(), reason="no Qt Multimedia")

_CHAPTERS = [{"start": 0, "title": "A"}, {"start": 60, "title": "B"}, {"start": 240, "title": "C"}]


def _player(monkeypatch=None, seen=None):
    if monkeypatch is not None:
        monkeypatch.setattr(
            "ui.player_widget.PlayerWidget.seek_to", lambda self, s: seen.append(s),
        )
    from ui.player_widget import PlayerWidget

    player = PlayerWidget()
    player.resize(800, 80)
    player.show()
    return player


def test_ticks_follow_the_chapters_and_the_fallback_duration(process_events):
    player = _player()
    player.set_chapters(_CHAPTERS, fallback_duration=300.0)
    process_events()
    marks = player._marks
    assert not marks.isHidden()
    xs = [marks.tick_x(start) for start, _ in marks._chapters]
    assert xs == sorted(xs) and xs[0] < xs[1] < xs[2]
    assert marks.chapter_at(xs[1]) == (60.0, "B")
    assert marks.chapter_at(xs[1] + 40) is None
    player.close()


def test_no_chapters_or_no_duration_hides_the_strip():
    player = _player()
    player.set_chapters(_CHAPTERS, fallback_duration=None)
    assert player._marks.isHidden()
    player.set_chapters([], fallback_duration=300.0)
    assert player._marks.isHidden()
    player.close()


def test_strip_lines_up_with_the_slider(process_events):
    player = _player()
    player.set_chapters(_CHAPTERS, fallback_duration=300.0)
    process_events()
    slider = player._slider.geometry()
    marks = player._marks.geometry()
    assert abs(marks.left() - slider.left()) <= 1
    assert abs(marks.right() - slider.right()) <= 1
    player.close()


def test_clicking_a_tick_seeks(monkeypatch, process_events):
    seen: list = []
    player = _player(monkeypatch, seen)
    player.set_chapters(_CHAPTERS, fallback_duration=300.0)
    process_events()
    x = player._marks.tick_x(240)
    QTest.mouseClick(player._marks, Qt.MouseButton.LeftButton, pos=QPoint(x, 4))
    assert seen == [240.0]
    player.close()


def test_main_window_puts_the_record_chapters_on_the_timeline():
    from ui.main_window import MainWindow

    win = MainWindow()
    win._document_session.apply_result(TranscriptionResult(
        segments=[Segment(0.0, 300.0, "hi")], language="en", duration=300.0,
    ))
    win.youtube_panel.set_provenance(51, "/media/talk.mp4")
    win.insights_panel.set_result({"chapters": [{"start": 30, "title": "Ins"}]})
    assert [t for _, t in win.player._chapter_marks] == ["Ins"]

    panel = win.youtube_panel
    panel.begin_generating()
    panel.set_result({"chapters": _CHAPTERS, "yt_titles": ["T"], "yt_description": ["H"],
                      "yt_tags": [], "yt_questions": []})
    assert [t for _, t in win.player._chapter_marks] == ["A", "B", "C"]
    panel._offset_spin.setValue(20)                      # video time only
    assert [s for s, _ in win.player._chapter_marks] == [0.0, 60.0, 240.0]
    assert win.player._marks._duration == 300.0

    panel.clear()
    win.insights_panel.clear()
    assert win.player._chapter_marks == []
    win.close()
