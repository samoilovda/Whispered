"""Proofreading mode (N01): the passages still to check, listening, marking,
flagging, and marks that survive a restart but not a text change."""

from __future__ import annotations

import pytest

import config
from core.history import HistoryStore
from core.i18n import load_locale
from transcriber import Segment, TranscriptionResult


def _result():
    return TranscriptionResult(segments=[
        Segment(0.0, 2.0, "alpha clear words", confidence=0.95),
        Segment(2.0, 4.0, "bravo doubtful words", confidence=0.40),
        Segment(4.0, 6.0, "charlie unknown words", confidence=None),
        Segment(6.0, 8.0, "delta doubtful again", confidence=0.30),
    ], language="en", duration=8.0)


@pytest.fixture
def setup(monkeypatch, tmp_path, process_events):
    from ui.main_window import MainWindow

    load_locale("en")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    rid = store.add(_result(), source_path="", source_name="talk.mp3")
    windows = []

    def make():
        window = MainWindow()
        window.show()
        process_events()
        windows.append(window)
        window._open_record_view(rid)
        process_events()
        return window

    yield make, store, rid, process_events
    for window in windows:
        window._revision_save_timer.stop()
        window.close()
    process_events()


def _todo(view):
    return view._uncertain_indices()


def test_only_known_low_confidence_needs_a_look(setup):
    make, *_ = setup
    view = make().transcript_view
    assert _todo(view) == [1, 3]          # not 2: unknown confidence is not "bad"
    assert "2" in view.uncertain_btn.text()


def test_stepping_selects_and_seeks_wrapping_around(setup):
    make, *_ = setup
    view = make().transcript_view
    seeks = []
    view.seek_requested.connect(seeks.append)
    view._open_review()
    assert seeks[-1] == 2.0 and not view._review_bar.isHidden()
    view.review_step(1)
    assert seeks[-1] == 6.0
    view.review_step(1)
    assert seeks[-1] == 2.0               # wrapped
    view.review_step(-1)
    assert seeks[-1] == 6.0


def test_checked_passages_leave_the_list_and_survive_a_restart(setup):
    make, store, rid, process_events = setup
    window = make()
    view = window.transcript_view
    view._open_review()
    view.mark_reviewed()                   # passage 1 checked, moves to 3
    assert _todo(view) == [3]
    assert view._result.segments[1].confidence == 0.40   # the original hint is untouched
    assert "1" in view._review_status.text() and "2" in view._review_status.text()
    window.close()

    fresh = make()
    assert _todo(fresh.transcript_view) == [3]


def test_correcting_a_checked_passage_puts_it_back(setup):
    make, store, rid, process_events = setup
    window = make()
    view = window.transcript_view
    view._open_review()
    view.mark_reviewed()
    view._result.segments[1].text = "bravo corrected words"
    view.result_changed.emit("text")
    assert _todo(view) == [1, 3]
    window._save_review_marks()
    assert store.get_review_marks(rid) is None     # the stale mark is not kept
    assert make().transcript_view._uncertain_indices() == [1, 3]


def test_flag_adds_a_confident_passage_and_unflag_removes_it(setup):
    make, *_ = setup
    view = make().transcript_view
    view._select_segment(0)
    view.toggle_flag()
    assert _todo(view) == [0, 1, 3]
    view._select_segment(0)
    view.toggle_flag()
    assert _todo(view) == [1, 3]


def test_filter_shows_only_what_is_left(setup):
    make, *_ = setup
    view = make().transcript_view
    view._review_only_chk.setChecked(True)
    text = view.text_edit.toPlainText()
    assert "bravo" in text and "delta" in text
    assert "alpha" not in text and "charlie" not in text
    view._review_only_chk.setChecked(False)
    assert "alpha" in view.text_edit.toPlainText()


def test_listen_asks_for_the_passage_and_stops_at_its_end(setup):
    make, *_ = setup
    window = make()
    view = window.transcript_view
    heard = []
    view.listen_requested.connect(lambda a, b, loop: heard.append((a, b, loop)))
    view._select_segment(1)
    view._review_loop.setChecked(True)
    view.listen_current()
    assert heard == [(2.0, 4.0, True)]

    played, paused, seeks = [], [], []
    window._source_filepath = "/media/talk.mp3"
    window.player.seek_to = lambda s: seeks.append(s)
    window.player.play = lambda: played.append(1)
    window.player.pause = lambda: paused.append(1)
    window._listen_range(2.0, 4.0, False)
    assert seeks == [2.0] and played
    window._on_player_position(3.0)
    assert not paused
    window._on_player_position(4.1)
    assert paused and window._listen is None
    window._listen_range(2.0, 4.0, True)
    window._on_player_position(4.1)
    assert seeks[-1] == 2.0               # looped, still listening
    assert window._listen is not None


def test_proofreading_keys_do_nothing_while_editing(setup):
    make, *_ = setup
    view = make().transcript_view
    hits = []
    view._enter_edit_mode()
    view._review_hotkey(lambda: hits.append(1))
    assert hits == []
    view._exit_edit_mode(save=False)
    view._review_hotkey(lambda: hits.append(1))
    assert hits == [1]


def test_a_clip_across_a_cut_plays_every_piece_in_order(setup):
    make, *_ = setup
    window = make()
    played, paused, seeks = [], [], []
    window._source_filepath = "/media/talk.mp3"
    window.player.seek_to = lambda s: seeks.append(s)
    window.player.play = lambda: played.append(1)
    window.player.pause = lambda: paused.append(1)
    window.clips_view.kept = lambda: [type("S", (), {"start": 0.0, "end": 10.0})(),
                                      type("S", (), {"start": 20.0, "end": 30.0})()]
    from domain import clips as c

    window.clips_view.title_edit.setText("Across")
    window.clips_view.start_edit.setText("00:00:05.000")
    window.clips_view.end_edit.setText("00:00:15.000")
    window.clips_view.timeline.setCurrentIndex(window.clips_view.timeline.findData(c.EDITED))
    window.clips_view._preview()                      # edited 5–15 = source 5–10 then 20–25
    assert seeks == [5.0]
    window._on_player_position(10.1)
    assert seeks == [5.0, 20.0] and not paused
    window._on_player_position(25.1)
    assert paused and window._listen is None and window._listen_queue == []
