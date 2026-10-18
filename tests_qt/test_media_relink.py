"""A record whose media moved (UI/UX audit 2026-10-18, UX-09 / T09)."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMessageBox

from core.history import HistoryStore
from core.i18n import load_locale, tr
from core.media_identity import media_fingerprint
from transcriber import Segment, TranscriptionResult


def _result():
    return TranscriptionResult(segments=[Segment(0.0, 60.0, "hello")], language="en", duration=60.0)


def _media(path, head=b"interview"):
    path.write_bytes(head + b"\0" * (2 << 20))
    return path


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
    original = _media(tmp_path / "interview.mp4")
    rid = store.add(_result(), source_path=str(original))
    store.set_media_fingerprint(rid, media_fingerprint(str(original)))
    window = MainWindow()
    window.show()
    process_events()
    yield window, store, rid, original, tmp_path
    window._revision_save_timer.stop()
    window.close()
    process_events()


def test_missing_source_shows_a_banner_and_keeps_the_transcript(setup, process_events):
    window, store, rid, original, tmp_path = setup
    original.rename(tmp_path / "moved.mp4")
    window._open_record_view(rid)
    process_events()
    assert not window.record_view.missing_source_row.isHidden()
    assert "hello" in window.transcript_view.get_result().full_text


def test_renamed_same_file_relinks_and_keeps_the_output_folder(setup, process_events):
    window, store, rid, original, tmp_path = setup
    moved = tmp_path / "renamed.mp4"
    original.rename(moved)
    window._open_record_view(rid)
    folder_before = window._artifact_source()
    window._on_media_checked(
        {"path": str(moved), "fingerprint": media_fingerprint(str(moved)), "duration": 60.0},
        rid, False,
    )
    process_events()
    record = store.get_record(rid)
    assert record["media_path"] == str(moved) and not record["media_changed_at"]
    assert window.record_view.missing_source_row.isHidden()
    assert window._artifact_source() == folder_before == str(original)  # results not "lost"
    assert window.file_selector.get_file() == str(moved)

    fresh_window_record = store.get_record(rid)
    assert fresh_window_record["source_path"] == str(original)


def test_other_video_of_the_same_length_needs_an_explicit_replacement(
    setup, monkeypatch, process_events,
):
    window, store, rid, original, tmp_path = setup
    original.unlink()
    other = _media(tmp_path / "other.mp4", head=b"a different talk")
    window._open_record_view(rid)
    answers = [QMessageBox.StandardButton.No]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answers.pop(0))
    info = {"path": str(other), "fingerprint": media_fingerprint(str(other)), "duration": 60.0}
    window._on_media_checked(info, rid, False)
    assert store.get_record(rid)["media_path"] == ""  # declined: nothing changed
    answers.append(QMessageBox.StandardButton.Yes)
    window._on_media_checked(info, rid, False)
    record = store.get_record(rid)
    assert record["media_path"] == str(other) and record["media_changed_at"]


def test_unreadable_file_changes_nothing(setup, monkeypatch, process_events):
    window, store, rid, original, tmp_path = setup
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    # Run the check synchronously: the worker's own run() on this thread.
    monkeypatch.setattr("core.media_check_worker.MediaCheckWorker.start", lambda self: self.run())
    window._open_record_view(rid)
    window._check_media(str(tmp_path / "does-not-exist.mp4"), rid, backfill=False)
    process_events()
    assert warnings and warnings[0].startswith(tr("record_relink_unreadable", error="")[:10])
    assert store.get_record(rid)["media_path"] == ""


def test_late_check_for_another_record_is_ignored(setup, process_events):
    window, store, rid, original, tmp_path = setup
    other = store.add(_result(), source_path="")
    window._open_record_view(other)
    window._on_media_checked({"path": str(original), "fingerprint": "v1:x", "duration": 60.0},
                             rid, False)
    assert store.get_record(rid)["media_path"] == ""
