"""ui/tray.py: the menu mirrors the window — status while busy, the
recorder toggle, recent records — and its actions are the window's."""

from __future__ import annotations

from core.history import HistoryStore
from core.i18n import load_locale, tr
from transcriber import Segment, TranscriptionResult


def test_menu_lists_actions_recent_records_and_status(monkeypatch, tmp_path, process_events):
    load_locale("en")
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    result = TranscriptionResult(segments=[Segment(0.0, 1.0, "x")], language="en", duration=1.0)
    rid = store.add(result, source_path="/media/talk.mp4", model="")
    from ui.main_window import MainWindow
    from ui.tray import TrayController

    window = MainWindow()
    status = {"text": ""}
    tray = TrayController(window, status_text=lambda: status["text"], recording=lambda: False)
    tray._rebuild()
    texts = [a.text() for a in tray.menu().actions()]
    assert tr("tray_start_recording") in texts and tr("tray_quit") in texts
    recent = next(a.menu() for a in tray.menu().actions() if a.menu() is not None)
    assert [a.text() for a in recent.actions()] == ["talk"]

    opened = []
    monkeypatch.setattr(window, "_open_record_view", lambda record_id, kind="": opened.append(record_id))
    recent.actions()[0].trigger()
    assert opened == [rid]

    status["text"] = "Transcribing… 40%"
    tray._rebuild()
    first = tray.menu().actions()[0]
    assert first.text() == "Transcribing… 40%" and not first.isEnabled()
    window.close()
    process_events()
