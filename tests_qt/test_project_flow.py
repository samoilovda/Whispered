"""Exporting and importing a project through the window (N06), with the
real worker thread: preview first, nothing left behind on cancel, a new
record on import, existing ones untouched."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QDialog, QFileDialog, QLabel, QMessageBox

import config
from core.history import HistoryStore
from core.i18n import load_locale
from transcriber import Segment, TranscriptionResult


def _result():
    return TranscriptionResult(segments=[Segment(0.0, 2.0, "portable words")], language="en", duration=2.0)


@pytest.fixture
def env(monkeypatch, tmp_path, process_events):
    from ui.main_window import MainWindow
    from ui.project_dialogs import ProjectPreviewDialog

    load_locale("en")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    shown = []
    monkeypatch.setattr(ProjectPreviewDialog, "exec",
                        lambda self: shown.append(self) or QDialog.DialogCode.Accepted)
    rid = store.add(_result(), source_path="", source_name="talk.mp3")
    store.set_title(rid, "Portable talk")
    window = MainWindow()
    window.show()
    process_events()
    window._open_record_view(rid)
    yield window, store, rid, shown, process_events
    window._revision_save_timer.stop()
    window.close()
    process_events()


def _wait(window, process_events):
    # A step may already have finished while the previous one's signals were
    # being delivered (the dry run hands over to the real work).
    worker = window.project._worker
    if worker is not None:
        assert worker.wait(10_000)
    process_events()
    process_events()


def test_export_shows_a_preview_then_writes_and_import_adds_a_new_record(env, monkeypatch, tmp_path):
    window, store, rid, shown, process_events = env
    out = tmp_path / "talk.whispered.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    window.project.export_open_record()
    _wait(window, process_events)                              # the dry run, off the GUI thread
    assert shown and "Portable talk" in shown[0].findChildren(QLabel)[0].text()
    _wait(window, process_events)                              # the export itself
    assert out.exists()

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(out), "")))
    window.project.import_project()
    _wait(window, process_events)                              # inspecting the archive
    _wait(window, process_events)                              # the import
    assert store.count() == 2
    assert window._last_record_id != rid                      # the new record is open
    assert window.transcript_view.get_result().full_text == "portable words"
    assert store.get_record(rid)["title"] == "Portable talk"   # the original is untouched
    assert store.get_record(window._last_record_id)["title"] == "Portable talk (imported)"


def test_a_refused_file_explains_why_and_changes_nothing(env, monkeypatch, tmp_path):
    window, store, rid, shown, process_events = env
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "")))
    window.project.import_project()
    _wait(window, process_events)
    assert warnings and "can't be imported" in warnings[0] and not shown
    assert store.count() == 1 and window.project._worker is None


def test_cancelling_an_export_leaves_no_file(env, monkeypatch, tmp_path):
    window, store, rid, shown, process_events = env
    out = tmp_path / "cancel.whispered.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    from application import project_archive as pa

    real = pa.write_export
    monkeypatch.setattr(pa, "write_export",
                        lambda plan, dest, **k: real(plan, dest, **{**k, "cancelled": lambda: True}))
    window.project.export_open_record()
    _wait(window, process_events)
    _wait(window, process_events)
    assert not out.exists() and not list(tmp_path.glob(".project.*"))


def test_nothing_to_export_without_a_saved_record(env, monkeypatch):
    window, store, rid, shown, process_events = env
    window._last_record_id = None
    window.project.export_open_record()
    assert not shown


def test_the_dry_runs_leave_the_gui_thread(env, monkeypatch, tmp_path):
    import threading

    from application import project_archive as pa

    window, store, rid, shown, process_events = env
    seen = {}
    real_plan, real_inspect = pa.plan_export, pa.inspect_archive
    monkeypatch.setattr(pa, "plan_export",
                        lambda *a, **k: seen.setdefault("plan", threading.current_thread()) and real_plan(*a, **k))
    monkeypatch.setattr(pa, "inspect_archive",
                        lambda *a, **k: seen.setdefault("inspect", threading.current_thread()) and real_inspect(*a, **k))
    out = tmp_path / "t.whispered.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    window.project.export_open_record()
    _wait(window, process_events)
    _wait(window, process_events)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(out), "")))
    window.project.import_project()
    _wait(window, process_events)
    _wait(window, process_events)
    main = threading.main_thread()
    assert seen["plan"] is not main and seen["inspect"] is not main


def test_import_preview_says_which_files_will_not_come_along(env, monkeypatch, tmp_path):
    import hashlib
    import json
    import zipfile

    from domain import project_manifest as pm

    window, store, rid, shown, process_events = env
    transcript = json.dumps({"json_payload": json.dumps({"segments": []}), "source_name": "a.mp3"}).encode()
    members = {"record/transcript.json": transcript, "artifacts/youtube_upload.queued.json": b"{}",
               "artifacts/oauth_token.json": b"{}"}
    files = [{"path": n, "size": len(d), "sha256": hashlib.sha256(d).hexdigest(),
              "kind": "record" if n.startswith("record/") else "artifact"} for n, d in members.items()]
    manifest = {"format": pm.FORMAT, "format_version": 1, "created_at": "", "app_version": "t",
                "record": {"title": "T"}, "counts": {}, "media": {"included": False}, "files": files}
    path = tmp_path / "hand.zip"
    with zipfile.ZipFile(path, "w") as z:
        for n, d in members.items():
            z.writestr(n, d)
        z.writestr(pm.MANIFEST_NAME, json.dumps(manifest))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), "")))
    window.project.import_project()
    _wait(window, process_events)
    texts = " ".join(label.text() for label in shown[0].findChildren(QLabel))
    assert "2 files with upload approvals or credentials will not be imported" in texts
    _wait(window, process_events)
