"""Real-Qt tests for B8 (docs/IMPROVEMENT_PLAN_2026-08.ru.md): transcript
versions and diff — MainWindow's debounced version save + restore, and
the TranscriptVersionsDialog itself.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMessageBox

from application.artifact_provenance import transcript_revision
from core.history import HistoryStore
from core.i18n import load_locale
from transcriber import Segment, TranscriptionResult
from ui.transcript_versions_dialog import TranscriptVersionsDialog


def _result(text="hello world", language="en"):
    return TranscriptionResult(
        segments=[Segment(0.0, 1.0, text, speaker=None)], language=language, duration=1.0,
    )


# ------------------------------------------------------------------ MainWindow wiring

@pytest.fixture
def window(monkeypatch, tmp_path, process_events):
    import config
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


def test_versions_button_disabled_until_a_result_is_loaded(window, process_events):
    assert not window.record_view.versions_btn.isEnabled()

    window.record_view.set_has_result(True)
    process_events()

    assert window.record_view.versions_btn.isEnabled()


def test_first_version_is_written_when_a_result_is_saved_to_history(
    window, process_events,
):
    from core.history import get_history_store

    window._save_to_history(_result(), source_path="", model="m", speaker_names={})
    process_events()

    store = get_history_store()
    metas = store.list_transcript_revisions(window._last_record_id)
    assert len(metas) == 1
    assert metas[0].word_count == 2


def test_editing_the_transcript_schedules_a_debounced_version_save(
    window, process_events,
):
    from core.history import get_history_store

    window._save_to_history(_result("original text"), source_path="", model="m", speaker_names={})
    process_events()
    record_id = window._last_record_id
    store = get_history_store()
    assert len(store.list_transcript_revisions(record_id)) == 1

    window.transcript_view.set_result(_result("edited text"))
    window._on_transcript_changed("text")
    process_events()

    # Not written yet — the timer hasn't fired.
    assert len(store.list_transcript_revisions(record_id)) == 1
    assert window._revision_save_timer.isActive()

    # Simulate the debounce elapsing without a real 5s wait.
    window._save_transcript_revision()
    process_events()

    metas = store.list_transcript_revisions(record_id)
    assert len(metas) == 2
    assert metas[0].word_count == 2  # "edited text"


def test_debounced_save_of_unchanged_content_does_not_add_a_version(
    window, process_events,
):
    from core.history import get_history_store

    window._save_to_history(_result("same text"), source_path="", model="m", speaker_names={})
    process_events()
    record_id = window._last_record_id
    store = get_history_store()

    window._save_transcript_revision()  # nothing changed since the first save
    process_events()

    assert len(store.list_transcript_revisions(record_id)) == 1


def test_restore_applies_via_document_session_to_every_consumer(
    window, process_events,
):
    """B8 acceptance criterion: restoring returns the text and updates
    every tab — checked here via DocumentSession's own fan-out (chat
    panel is one of its registered consumers)."""
    window._save_to_history(_result("version one"), source_path="", model="m", speaker_names={})
    process_events()
    record_id = window._last_record_id

    from core.history import get_history_store
    store = get_history_store()
    first_revision_id = store.list_transcript_revisions(record_id)[0].id

    window.transcript_view.set_result(_result("version two"))
    window._on_transcript_changed("text")
    window._save_transcript_revision()
    process_events()

    window._restore_transcript_revision(first_revision_id)
    process_events()

    assert window.transcript_view.get_result().full_text == "version one"
    # chat_panel is a registered DocumentSession consumer — restoring
    # through apply_result() must reach it too, not just the transcript tab.
    assert window.chat_panel._transcript == "version one"


def test_restore_persists_to_history_and_adds_a_new_version(window, process_events):
    from core.history import get_history_store

    window._save_to_history(_result("version one"), source_path="", model="m", speaker_names={})
    process_events()
    record_id = window._last_record_id
    store = get_history_store()
    first_revision_id = store.list_transcript_revisions(record_id)[0].id

    window.transcript_view.set_result(_result("version two"))
    window._on_transcript_changed("text")
    window._save_transcript_revision()
    process_events()
    assert len(store.list_transcript_revisions(record_id)) == 2

    window._restore_transcript_revision(first_revision_id)
    process_events()

    metas = store.list_transcript_revisions(record_id)
    assert len(metas) == 3
    record = store.get_record(record_id)
    assert record["payload"]["segments"][0]["text"] == "version one"


def test_restore_changes_transcript_revision_so_cached_steps_would_rerun(
    window, process_events,
):
    """B8 item 5's own acceptance note: restoring an old version changes
    transcript_revision (what build_cache_checks() keys artifact
    validity on), so a recipe rerun after a restore does not just skip
    every step from a stale cache."""
    window._save_to_history(_result("version one"), source_path="", model="m", speaker_names={})
    window.transcript_view.set_result(_result("version one"))
    process_events()
    record_id = window._last_record_id
    from core.history import get_history_store
    store = get_history_store()
    first_revision_id = store.list_transcript_revisions(record_id)[0].id
    rev_before_restore = transcript_revision(
        window.transcript_view.get_result().segments, "en",
    )

    window.transcript_view.set_result(_result("version two"))
    window._on_transcript_changed("text")
    window._save_transcript_revision()
    process_events()
    rev_after_edit = transcript_revision(window.transcript_view.get_result().segments, "en")
    assert rev_after_edit != rev_before_restore

    window._restore_transcript_revision(first_revision_id)
    process_events()
    rev_after_restore = transcript_revision(window.transcript_view.get_result().segments, "en")

    assert rev_after_restore == rev_before_restore
    assert rev_after_restore != rev_after_edit


def test_restore_of_an_unknown_revision_id_does_not_raise(window, process_events):
    window._save_to_history(_result(), source_path="", model="m", speaker_names={})
    process_events()

    window._restore_transcript_revision(999999)  # must not raise
    process_events()


def test_restore_without_an_open_record_is_a_noop(window, process_events):
    window._restore_transcript_revision(1)  # _last_record_id is still None
    process_events()


# ------------------------------------------------------------------ TranscriptVersionsDialog

def _make_store(tmp_path):
    return HistoryStore(db_path=tmp_path / "history.sqlite3")


def test_dialog_lists_versions_newest_first(monkeypatch, tmp_path, process_events):
    load_locale("en")
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = store.add(_result(), source_path="", model="")
    import json
    store.add_transcript_revision(
        record_id, "rev1", json.dumps({"segments": [{"text": "first"}], "language": "en"}),
    )
    store.add_transcript_revision(
        record_id, "rev2", json.dumps({"segments": [{"text": "second"}], "language": "en"}),
    )

    dialog = TranscriptVersionsDialog(record_id)
    process_events()

    assert dialog._list.count() == 2
    # newest first: rev2 ("second") was inserted after rev1 ("first")
    assert dialog._revisions[0].revision == "rev2"
    assert dialog._revisions[1].revision == "rev1"
    dialog.close()


def test_selecting_two_rows_shows_a_diff_with_the_marker_word(
    monkeypatch, tmp_path, process_events,
):
    load_locale("en")
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = store.add(_result(), source_path="", model="")
    import json
    store.add_transcript_revision(
        record_id, "rev1", json.dumps({"segments": [{"text": "hello"}], "language": "en"}),
    )
    store.add_transcript_revision(
        record_id, "rev2",
        json.dumps({"segments": [{"text": "hello zorblaxian"}], "language": "en"}),
    )

    dialog = TranscriptVersionsDialog(record_id)
    process_events()

    dialog._list.selectAll()
    process_events()

    assert "zorblaxian" in dialog._diff_view.toPlainText()
    dialog.close()


def test_selecting_one_row_enables_restore_and_emits_on_confirm(
    monkeypatch, tmp_path, process_events,
):
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = store.add(_result(), source_path="", model="")
    import json
    version_id = store.add_transcript_revision(
        record_id, "rev1", json.dumps({"segments": [{"text": "hello"}], "language": "en"}),
    )

    dialog = TranscriptVersionsDialog(record_id)
    process_events()

    dialog._list.item(0).setSelected(True)
    process_events()
    assert dialog._restore_btn.isEnabled()

    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    seen = []
    dialog.restore_requested.connect(seen.append)
    dialog._restore_btn.click()
    process_events()

    assert seen == [version_id]
    dialog.close()


def test_declining_the_restore_confirmation_does_not_emit(
    monkeypatch, tmp_path, process_events,
):
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = store.add(_result(), source_path="", model="")
    import json
    store.add_transcript_revision(
        record_id, "rev1", json.dumps({"segments": [{"text": "hello"}], "language": "en"}),
    )

    dialog = TranscriptVersionsDialog(record_id)
    process_events()
    dialog._list.item(0).setSelected(True)
    process_events()

    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    seen = []
    dialog.restore_requested.connect(seen.append)
    dialog._restore_btn.click()
    process_events()

    assert seen == []
    dialog.close()


def test_reload_reflects_a_newly_added_version(monkeypatch, tmp_path, process_events):
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = store.add(_result(), source_path="", model="")
    import json
    store.add_transcript_revision(
        record_id, "rev1", json.dumps({"segments": [{"text": "hello"}], "language": "en"}),
    )

    dialog = TranscriptVersionsDialog(record_id)
    process_events()
    assert dialog._list.count() == 1

    store.add_transcript_revision(
        record_id, "rev2", json.dumps({"segments": [{"text": "hello again"}], "language": "en"}),
    )
    dialog.reload()
    process_events()

    assert dialog._list.count() == 2
    dialog.close()


def test_dialog_with_no_versions_shows_an_empty_list(monkeypatch, tmp_path, process_events):
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    record_id = store.add(_result(), source_path="", model="")

    dialog = TranscriptVersionsDialog(record_id)
    process_events()

    assert dialog._list.count() == 0
    assert not dialog._restore_btn.isEnabled()
    dialog.close()
