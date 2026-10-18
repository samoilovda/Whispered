"""A record's cut survives navigation, restarts and transcript edits
(UI/UX audit 2026-10-18, UX-06 / T06).

Before: cut segment 1 in A → open B → back to A, and cut_indices() went
from [1] to [] — every load re-checked all segments.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMessageBox

from core.history import HistoryStore
from core.i18n import load_locale
from transcriber import Segment, TranscriptionResult


def _result(prefix: str) -> TranscriptionResult:
    return TranscriptionResult(
        segments=[Segment(0.0, 2.0, f"{prefix} one", speaker="A"),
                  Segment(2.0, 4.0, f"{prefix} two", speaker="B"),
                  Segment(4.0, 6.0, f"{prefix} three", speaker="A")],
        language="en", duration=6.0,
    )


@pytest.fixture
def setup(monkeypatch, tmp_path, process_events):
    import config
    from ui.main_window import MainWindow

    load_locale("en")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    a = store.add(_result("a"), source_path="", source_name="a.mp3")
    b = store.add(_result("b"), source_path="", source_name="b.mp3")
    windows = []

    def make():
        win = MainWindow()
        win.show()
        process_events()
        windows.append(win)
        return win

    yield make, store, a, b
    for win in windows:
        win._revision_save_timer.stop()
        win.close()
    process_events()


def test_cut_survives_a_round_trip_and_a_restart(setup, process_events):
    make, store, a, b = setup
    window = make()
    window._open_record_view(a)
    window.cut_view.set_cut([1], True)
    window._open_record_view(b)
    assert window.cut_view.cut_indices() == set()  # B's own cut, not A's
    window._open_record_view(a)
    assert window.cut_view.cut_indices() == {1}

    fresh = make()
    fresh._open_record_view(a)
    assert fresh.cut_view.cut_indices() == {1}


def test_text_edit_keeps_the_cut(setup, process_events):
    make, store, a, _b = setup
    window = make()
    window._open_record_view(a)
    window.cut_view.set_cut([2], True)
    result = window.transcript_view.get_result()
    result.segments[2].text = "a three, corrected"
    window.transcript_view.result_changed.emit("text")
    assert window.cut_view.cut_indices() == {2}
    assert not window.cut_view.needs_review()


def test_undo_redo_cover_checkbox_select_and_pauses(setup, process_events):
    make, _store, a, _b = setup
    window = make()
    window._open_record_view(a)
    view = window.cut_view
    view.set_cut([0], True)
    view._select_none()
    assert view.cut_indices() == {0, 1, 2}
    view.undo()
    assert view.cut_indices() == {0}
    view.undo()
    assert view.cut_indices() == set()
    view.redo()
    assert view.cut_indices() == {0}
    from PyQt6.QtCore import Qt

    view._list.item(2).setCheckState(Qt.CheckState.Unchecked)  # a checkbox click
    assert view.cut_indices() == {0, 2}
    view.undo()
    assert view.cut_indices() == {0}


def test_split_line_flags_review_and_export_asks(setup, monkeypatch, process_events):
    make, store, a, _b = setup
    window = make()
    window._open_record_view(a)
    window.cut_view.set_cut([1], True)
    result = window.transcript_view.get_result()
    result.segments[1:2] = [Segment(2.0, 3.0, "a", speaker="B"), Segment(3.0, 4.0, "two", speaker="B")]
    window.transcript_view.result_changed.emit("structure")
    view = window.cut_view
    assert view.needs_review()
    assert view.cut_indices() == set()  # not guessed
    assert not view._review_row.isHidden()

    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.No)
    window._export_edl()
    assert asked  # a cut needing review is never exported silently

    # The decision is stored with the record until reviewed.
    fresh = make()
    fresh._open_record_view(a)
    assert fresh.cut_view.needs_review()
    fresh.cut_view.accept_review()
    assert not fresh.cut_view.needs_review()
    assert not store.get_cut_plan(a)  # nothing left to keep


def test_deleting_a_record_removes_its_cut(setup, process_events):
    make, store, a, _b = setup
    window = make()
    window._open_record_view(a)
    window.cut_view.set_cut([0], True)
    assert store.get_cut_plan(a)
    store.delete(a)
    assert store.get_cut_plan(a) is None
