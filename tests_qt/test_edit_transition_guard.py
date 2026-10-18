"""An open transcript edit survives every way of leaving the record
(UI/UX audit 2026-10-18, UX-05 / T05).

Before: open A → Edit → change text → open B → back to A, and the change
was gone from both the screen and the database.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMessageBox

from core.history import HistoryStore
from core.i18n import load_locale
from transcriber import Segment, TranscriptionResult


def _result(text: str) -> TranscriptionResult:
    return TranscriptionResult(
        segments=[Segment(0.0, 2.0, text, speaker="SPEAKER_00"),
                  Segment(2.0, 4.0, "second", speaker="SPEAKER_01")],
        language="en", duration=4.0,
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
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda parent, title, text, *a, **k: warnings.append(text))
    a = store.add(_result("original A"), source_path="", source_name="a.mp3")
    b = store.add(_result("original B"), source_path="", source_name="b.mp3")
    win = MainWindow()
    win.show()
    process_events()
    yield win, store, a, b, warnings
    win._revision_save_timer.stop()
    win._draft_timer.stop()
    if win.transcript_view.is_editing():
        win.transcript_view._exit_edit_mode(save=False)
    win.close()
    process_events()


def _edit(window, old: str, new: str) -> None:
    view = window.transcript_view
    view._enter_edit_mode()
    view.text_edit.setPlainText(view.text_edit.toPlainText().replace(old, new))


def _stored_text(store, record_id) -> str:
    return " ".join(seg["text"] for seg in store.get_record(record_id)["payload"]["segments"])


def test_switching_records_saves_the_open_edit_for_its_own_record(setup, process_events):
    window, store, a, b, warnings = setup
    window._open_record_view(a)
    _edit(window, "original A", "edited A")

    window._open_record_view(b)
    process_events()
    assert window._last_record_id == b
    assert "edited A" in _stored_text(store, a)
    assert "edited A" not in _stored_text(store, b)

    window._open_record_view(a)
    process_events()
    assert "edited A" in window.transcript_view.get_result().full_text
    assert warnings == []


def test_debounced_version_lands_on_the_edited_record(setup, process_events):
    window, store, a, b, _ = setup
    window._open_record_view(a)
    before_a = len(store.list_transcript_revisions(a))
    before_b = len(store.list_transcript_revisions(b))
    _edit(window, "original A", "edited A")
    window._open_record_view(b)  # flushes the 5s timer before leaving
    process_events()
    assert not window._revision_save_timer.isActive()
    assert len(store.list_transcript_revisions(a)) == before_a + 1
    assert len(store.list_transcript_revisions(b)) == before_b


def test_new_record_and_close_commit_too(setup, process_events):
    window, store, a, _b, _ = setup
    window._open_record_view(a)
    _edit(window, "original A", "via new record")
    window._show_new_draft()
    assert window._stack.currentIndex() == window._start_index
    assert "via new record" in _stored_text(store, a)

    window._open_record_view(a)
    _edit(window, "via new record", "via close")
    window.close()
    process_events()
    assert "via close" in _stored_text(store, a)


def test_invalid_buffer_keeps_the_user_on_the_record(setup, process_events):
    window, store, a, b, warnings = setup
    window._open_record_view(a)
    view = window.transcript_view
    view._enter_edit_mode()
    view.text_edit.setPlainText("[00:00:0x.000] broken\n[00:00:02.000] second")

    window._open_record_view(b)
    process_events()
    assert window._last_record_id == a
    assert view.is_editing()
    assert "broken" in view.text_edit.toPlainText()
    assert warnings and "Line 1" in warnings[0]
    assert "original A" in _stored_text(store, a)


def test_storage_error_does_not_lose_the_text(setup, monkeypatch, process_events):
    window, store, a, b, warnings = setup
    window._open_record_view(a)
    _edit(window, "original A", "unsaved A")

    def boom(*_a, **_k):
        raise OSError("disk full")

    original_update = store.update_result
    monkeypatch.setattr(store, "update_result", boom)
    window._open_record_view(b)
    process_events()
    assert window._last_record_id == a
    assert "unsaved A" in window.transcript_view.get_result().full_text
    assert warnings and "disk full" in warnings[0]
    assert window.record_view.save_label.text() == "✕ Not saved"

    # Storage is back: leaving retries the write first, then goes.
    monkeypatch.setattr(store, "update_result", original_update)
    window._open_record_view(b)
    process_events()
    assert window._last_record_id == b
    assert "unsaved A" in _stored_text(store, a)


def test_no_op_edit_creates_no_version(setup, process_events):
    window, store, a, _b, _ = setup
    window._open_record_view(a)
    before = len(store.list_transcript_revisions(a))
    window.transcript_view._enter_edit_mode()
    window._show_new_draft()
    assert len(store.list_transcript_revisions(a)) == before


def test_crash_draft_is_offered_as_a_draft_after_restart(setup, monkeypatch, process_events):
    window, store, a, _b, _ = setup
    window._open_record_view(a)
    _edit(window, "original A", "typed before crash")
    window._save_edit_draft()  # what the 1.5s autosave does
    assert store.get_draft(a)["buffer"].count("typed before crash") == 1
    assert "original A" in _stored_text(store, a)  # the draft is not the saved text

    # "Restart": a fresh window opening A finds the draft and offers it.
    from ui.main_window import MainWindow

    window.transcript_view._leave_edit_mode = lambda: None  # simulate the crash: no cleanup
    asked = []
    monkeypatch.setattr(MainWindow, "_ask_restore_draft",
                        lambda self, stamp: asked.append(stamp) or True)
    fresh = MainWindow()
    fresh.show()
    fresh._open_record_view(a)
    process_events()
    assert asked
    assert fresh.transcript_view.is_editing()
    assert "typed before crash" in fresh.transcript_view.edit_buffer()
    # Cancelling the recovered edit drops the draft.
    fresh.transcript_view._exit_edit_mode(save=False)
    assert store.get_draft(a) is None
    fresh._revision_save_timer.stop()
    fresh.close()
    process_events()
