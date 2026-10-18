"""The Live presentation window (N08): it only listens to the session,
never duplicates or downgrades a final, and closing it never stops the
recording."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox

import config
from core.history import HistoryStore
from core.i18n import load_locale, tr
from core.live.contracts import SegmentState, SegmentUpdate
from transcriber import Segment


def _update(sid, revision, state, text, speaker="mic"):
    return SegmentUpdate(sid, revision, state, Segment(0.0, 2.0, text, speaker=speaker))


@pytest.fixture
def win(process_events):
    from ui.live_presentation_window import LivePresentationWindow

    load_locale("en")
    w = LivePresentationWindow()
    w.show()
    process_events()
    yield w
    w.close()


def test_partial_is_marked_and_final_replaces_it_once(win):
    win.accept_update(_update("mic:1", 1, SegmentState.PARTIAL, "hello wor"))
    assert "hello wor …" in win.plain_text() and tr("live_partial") in win.plain_text()
    win.accept_update(_update("mic:1", 2, SegmentState.FINAL, "hello world"))
    text = win.plain_text()
    assert text.count("hello world") == 1 and "…" not in text
    # A late draft or a repeated final never undoes or doubles it.
    assert not win.accept_update(_update("mic:1", 3, SegmentState.PARTIAL, "hello"))
    assert not win.accept_update(_update("mic:1", 2, SegmentState.FINAL, "hello world"))
    assert win.plain_text().count("hello world") == 1 and "hello wor " not in win.plain_text()


def test_state_and_source_are_shown(win):
    win.set_session_state("running")
    assert win.state_label.text() == tr("livepres_state_recording") and win.pause_btn.isEnabled()
    win.accept_update(_update("system:1", 1, SegmentState.FINAL, "from the call", speaker="system"))
    assert win.source_label.text() == tr("live_zoom")
    win.set_source_state("system", "failed")
    assert tr("livepres_source_lost", source=tr("live_zoom")) in win.source_label.text()
    win.set_session_state("paused")
    assert win.state_label.text() == tr("livepres_state_paused")
    win.set_session_state("completed")
    assert win.state_label.text() == tr("livepres_state_stopped")
    assert not win.pause_btn.isEnabled() and not win.stop_btn.isEnabled()


def test_keyboard_pause_size_and_close_do_not_stop(win):
    events = []
    win.pause_requested.connect(lambda paused: events.append(("pause", paused)))
    win.stop_requested.connect(lambda: events.append(("stop",)))
    win.set_session_state("running")
    QTest.keyClick(win, Qt.Key.Key_Space)
    win.set_session_state("paused")
    QTest.keyClick(win, Qt.Key.Key_Space)
    before = win.point_size()
    QTest.keyClick(win, Qt.Key.Key_Plus)
    assert win.point_size() == before + 4
    QTest.keyClick(win, Qt.Key.Key_0)
    assert win.point_size() == before
    QTest.keyClick(win, Qt.Key.Key_Escape)
    assert not win.isVisible()
    assert events == [("pause", True), ("pause", False)]


def test_stop_needs_confirmation(win, monkeypatch):
    stops = []
    win.stop_requested.connect(lambda: stops.append(1))
    win.set_session_state("running")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    win.stop_btn.click()
    assert stops == []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.stop_btn.click()
    assert stops == [1]


def test_size_is_clamped_and_text_is_escaped(win):
    win.set_point_size(1000)
    assert win.point_size() == 96
    win.set_point_size(1)
    assert win.point_size() == 16
    win.accept_update(_update("mic:2", 1, SegmentState.FINAL, "<b>bold</b> & more"))
    assert "<b>bold</b> & more" in win.plain_text()


@pytest.fixture
def main(monkeypatch, tmp_path, process_events):
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    monkeypatch.setattr(config, "_config", config.Config())
    from ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    process_events()
    yield w
    w.close()
    process_events()


def test_the_flag_gates_the_button_and_the_window(main, process_events):
    session = main.live_session
    assert main.live_view.present_btn.isHidden()
    session.open_presentation()
    assert session.presentation is None
    config.get_config().live_presentation_enabled = True
    session.apply_presentation_flag()
    assert not main.live_view.present_btn.isHidden()
    session.open_presentation()
    assert session.presentation is not None and session.presentation.isVisible()
    # Updates reach it through the existing session stream — no second pipeline.
    session.on_segment_update(_update("mic:9", 1, SegmentState.PARTIAL, "live words"))
    assert "live words" in session.presentation.plain_text()
    # Closing it leaves the runtime alone.
    state_before = main.live_runtime.session_state
    session.presentation.close()
    assert main.live_runtime.session_state == state_before
    config.get_config().live_presentation_enabled = False
    session.apply_presentation_flag()
