"""Real-Qt tests for the speaker-rename dialog's alias suggestions (B6,
see docs/IMPROVEMENT_PLAN_2026-08.ru.md) and the end-to-end path: rename
a speaker in one record, see that name suggested when renaming a speaker
in a different one.
"""

from __future__ import annotations

import pytest


def test_dialog_combo_offers_suggestions_without_overwriting_the_current_text(
    process_events,
):
    """B6 acceptance criterion: a suggestion never self-inserts — the
    combo's starting text is exactly the existing display name, even
    though the dropdown also lists prior aliases."""
    from ui.transcript_view import _SpeakerRenameDialog

    dlg = _SpeakerRenameDialog(
        {"Speaker 1": "Speaker 1"}, suggestions=["Alice", "Bob"],
    )
    process_events()

    combo = dlg._combos["Speaker 1"]
    assert combo.currentText() == "Speaker 1"
    items = [combo.itemText(i) for i in range(combo.count())]
    assert items == ["Alice", "Bob"]

    dlg.close()


def test_dialog_get_names_reads_whatever_the_combo_currently_shows(process_events):
    from ui.transcript_view import _SpeakerRenameDialog

    dlg = _SpeakerRenameDialog(
        {"Speaker 1": "Speaker 1", "Speaker 2": "Speaker 2"},
        suggestions=["Alice"],
    )
    process_events()

    dlg._combos["Speaker 1"].setCurrentText("Alice")
    # Speaker 2 left untouched — falls back to its own id.
    names = dlg.get_names()

    assert names == {"Speaker 1": "Alice", "Speaker 2": "Speaker 2"}
    dlg.close()


def test_dialog_with_no_suggestions_has_an_empty_dropdown(process_events):
    from ui.transcript_view import _SpeakerRenameDialog

    dlg = _SpeakerRenameDialog({"Speaker 1": "Speaker 1"})
    process_events()

    combo = dlg._combos["Speaker 1"]
    assert combo.count() == 0
    assert combo.currentText() == "Speaker 1"

    dlg.close()


# ------------------------------------------------------------------ end-to-end via MainWindow

def _result():
    from transcriber import Segment, TranscriptionResult

    return TranscriptionResult(
        segments=[
            Segment(0.0, 1.0, "hello", speaker="Speaker 1"),
            Segment(1.0, 2.0, "world", speaker="Speaker 2"),
        ],
        language="en", duration=2.0,
    )


@pytest.fixture
def window(monkeypatch, tmp_path, process_events):
    import config
    from core.history import HistoryStore
    from ui.main_window import MainWindow

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())

    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    win = MainWindow()
    yield win
    win.close()
    process_events()


def test_renaming_a_speaker_in_one_record_suggests_it_for_another(
    window, monkeypatch, process_events,
):
    """B6 acceptance criterion, verbatim: rename a speaker in record A,
    open record B — that name is first in the suggestions list."""
    from core.history import get_history_store
    from PyQt6.QtWidgets import QDialog
    from ui.transcript_view import _SpeakerRenameDialog

    store = get_history_store()
    record_a = store.add(_result(), source_path="", model="", source_name="a.mp3")
    window._last_record_id = record_a
    window.transcript_view.set_result(_result())

    def fake_exec_rename_to_alice(self):
        self._combos["Speaker 1"].setCurrentText("Alice")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_SpeakerRenameDialog, "exec", fake_exec_rename_to_alice)
    window.transcript_view._rename_speakers()
    process_events()

    assert store.list_speaker_aliases() == ["Alice"]

    # Record B: opening the rename dialog must offer "Alice" first, and
    # must not have applied it on its own (Speaker 1 still reads as
    # itself, per get_names()'s "unedited falls back to the id" rule).
    record_b = store.add(_result(), source_path="", model="", source_name="b.mp3")
    window._last_record_id = record_b
    window.transcript_view.set_result(_result())

    seen_suggestions = []
    seen_starting_text = []

    def fake_exec_capture(self):
        combo = self._combos["Speaker 1"]
        seen_suggestions.append([combo.itemText(i) for i in range(combo.count())])
        seen_starting_text.append(combo.currentText())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(_SpeakerRenameDialog, "exec", fake_exec_capture)
    window.transcript_view._rename_speakers()
    process_events()

    assert seen_suggestions and seen_suggestions[0][:1] == ["Alice"]
    assert seen_starting_text == ["Speaker 1"]


def test_unedited_speaker_ids_are_not_remembered_as_aliases(window, process_events):
    """A rename dialog's untouched fields fall back to the raw speaker id
    (see _SpeakerRenameDialog.get_names()) — those must not pollute the
    suggestion list."""
    from core.history import get_history_store

    store = get_history_store()
    record_id = store.add(_result(), source_path="", model="")
    window._last_record_id = record_id
    window.transcript_view.set_result(_result())

    result = window.transcript_view.get_result()
    # Speaker 1 renamed, Speaker 2 left as its own id (as get_names()
    # would produce when that field wasn't touched).
    result.speaker_names = {"Speaker 1": "Alice", "Speaker 2": "Speaker 2"}
    window.transcript_view.result_changed.emit("speakers")
    process_events()

    assert store.list_speaker_aliases() == ["Alice"]
