"""The chat belongs to a record (UI/UX audit 2026-10-18, UX-07 / T07).

Before: the conversation lived in memory, was wiped by A → B → A, and two
records with the same text shared one conversation (identity by text).
A fake worker stands in for LM Studio; signals are emitted by hand.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

from core.history import HistoryStore
from core.i18n import load_locale
from transcriber import Segment, TranscriptionResult


class _FakeWorker(QObject):
    token_received = pyqtSignal(str)
    turn_finished = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    finished = pyqtSignal()
    instances: list["_FakeWorker"] = []

    def __init__(self, messages, lm_url, parent=None, **_kwargs):
        super().__init__(parent)
        self.messages = messages
        self._running = False
        _FakeWorker.instances.append(self)

    def start(self):
        self._running = True

    def isRunning(self):  # noqa: N802 - QThread API
        return self._running

    def cancel(self):
        self._running = False

    def wait(self, *_a):
        return True

    def done(self, text):
        self._running = False
        self.turn_finished.emit(text)


def _result(text="same words in both records"):
    return TranscriptionResult(segments=[Segment(0.0, 3.0, text)], language="en", duration=3.0)


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
    monkeypatch.setattr("core.chat_worker.ChatWorker", _FakeWorker)
    _FakeWorker.instances.clear()
    a = store.add(_result(), source_path="", source_name="a.mp3")
    b = store.add(_result(), source_path="", source_name="b.mp3")
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


def _ask(window, question, answer=None):
    window.chat_panel._send(question)
    worker = _FakeWorker.instances[-1]
    if answer is not None:
        worker.done(answer)
    return worker


def _contents(panel):
    return [(m["role"], m["content"], m["status"]) for m in panel._history]


def test_same_text_different_records_are_different_conversations(setup, process_events):
    make, store, a, b = setup
    window = make()
    window._open_record_view(a)
    _ask(window, "question for A", "answer for A")
    window._open_record_view(b)
    assert _contents(window.chat_panel) == []
    window._open_record_view(a)
    assert _contents(window.chat_panel) == [
        ("user", "question for A", "complete"), ("assistant", "answer for A", "complete"),
    ]
    assert store.list_chat_messages(b) == []


def test_conversation_survives_a_restart(setup, process_events):
    make, store, a, _b = setup
    window = make()
    window._open_record_view(a)
    _ask(window, "kept?", "yes")
    fresh = make()
    fresh._open_record_view(a)
    assert [m["content"] for m in fresh.chat_panel._history] == ["kept?", "yes"]


def test_late_answer_of_the_previous_record_never_lands(setup, process_events):
    make, store, a, b = setup
    window = make()
    window._open_record_view(a)
    worker = _ask(window, "slow question")
    window._open_record_view(b)  # A's worker is cancelled/retired
    worker.token_received.emit("late token")
    worker.turn_finished.emit("late answer")
    assert _contents(window.chat_panel) == []
    assert all(m["content"] != "late answer" for m in store.list_chat_messages(b))
    assert all(m["content"] != "late answer" for m in store.list_chat_messages(a))


def test_stopped_answer_is_kept_as_incomplete_and_not_sent_as_context(setup, process_events):
    make, store, a, _b = setup
    window = make()
    window._open_record_view(a)
    worker = _ask(window, "long one")
    worker.token_received.emit("partial ans")
    window.chat_panel._on_stop()
    assert _contents(window.chat_panel)[-1] == ("assistant", "partial ans", "cancelled")
    nxt = _ask(window, "next")
    assert all(m.get("content") != "partial ans" for m in nxt.messages)


def test_failed_turn_is_marked_failed(setup, process_events):
    make, store, a, _b = setup
    window = make()
    window._open_record_view(a)
    worker = _ask(window, "will fail")
    worker.error_occurred.emit("LM Studio is not responding")
    assert store.list_chat_messages(a)[0]["status"] == "failed"


def test_transcript_edit_keeps_the_conversation_and_marks_the_change(setup, process_events):
    make, _store, a, _b = setup
    window = make()
    window._open_record_view(a)
    _ask(window, "before edit", "old answer")
    result = window.transcript_view.get_result()
    result.segments[0].text = "edited words"
    window.transcript_view.result_changed.emit("text")
    panel = window.chat_panel
    assert [m["content"] for m in panel._history] == ["before edit", "old answer"]
    from PyQt6.QtWidgets import QLabel
    from core.i18n import tr

    assert any(label.text() == tr("chat_revision_changed")
               for label in panel._msg_container.findChildren(QLabel) if not label.isHidden())


def test_history_off_says_so_and_keeps_nothing(setup, monkeypatch, process_events):
    import config

    make, store, a, _b = setup
    window = make()
    config._config.history_enabled = False
    window._open_record_view(a)
    _ask(window, "ephemeral", "gone")
    assert not window.chat_panel._not_saved_label.isHidden()
    assert store.list_chat_messages(a) == []


def test_deleting_the_record_deletes_its_conversation(setup, process_events):
    make, store, a, _b = setup
    window = make()
    window._open_record_view(a)
    _ask(window, "q", "a")
    store.delete(a)
    assert store.list_chat_messages(a) == []


def test_export_markdown_marks_incomplete_answers(setup, process_events):
    from ui.chat_panel import conversation_markdown

    text = conversation_markdown([
        {"role": "user", "content": "q", "status": "complete", "revision": "r1"},
        {"role": "assistant", "content": "part", "status": "cancelled", "revision": "r1"},
    ])
    assert "**You:** q" in text and "incomplete" in text
