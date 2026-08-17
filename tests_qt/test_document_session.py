"""Acceptance tests for R6's first step (DocumentSession.apply_result):
every registered consumer must see a new result on each of the paths that
produce or load one — the same fan-out list, not three independently
maintained copies. See docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R6, and
application/document_session.py.
"""

from __future__ import annotations


def _spy(monkeypatch, obj, method_name):
    calls = []
    original = getattr(obj, method_name)

    def wrapper(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(obj, method_name, wrapper)
    return calls


def _consumer_spies(monkeypatch, window):
    return {
        "chat": _spy(monkeypatch, window.chat_panel, "set_transcript"),
        "insights": _spy(monkeypatch, window.insights_panel, "set_segments"),
        "youtube": _spy(monkeypatch, window.youtube_panel, "set_segments"),
        "cover": _spy(monkeypatch, window.cover_view, "set_segments"),
        "cut": _spy(monkeypatch, window.cut_view, "set_result"),
    }


def test_fresh_transcription_reaches_every_registered_consumer(monkeypatch, process_events):
    from transcriber import Segment, TranscriptionResult
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()
    spies = _consumer_spies(monkeypatch, window)

    result = TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello world")],
        language="en",
        duration=1.0,
    )
    window._on_finished(result, open_record=True, save_history=False)
    process_events()

    for name, calls in spies.items():
        assert len(calls) == 1, f"{name} consumer called {len(calls)} times, expected 1"
    assert spies["cover"][0][0] == (result.segments,)

    window.close()
    process_events()


def test_history_open_reaches_every_registered_consumer(monkeypatch, process_events, tmp_path):
    from core.history import get_history_store
    from transcriber import Segment, TranscriptionResult
    from ui.main_window import MainWindow

    result = TranscriptionResult(
        segments=[Segment(0.0, 1.0, "archived text")],
        language="en",
        duration=1.0,
    )
    record_id = get_history_store().add(result, source_path="", source_kind="file", source_name="rec")

    window = MainWindow()
    window.show()
    process_events()
    spies = _consumer_spies(monkeypatch, window)

    assert window._load_from_history(record_id) is True
    process_events()

    for name, calls in spies.items():
        assert len(calls) == 1, f"{name} consumer called {len(calls)} times, expected 1"
    # Regression coverage for the audit bug this refactor targets: Cover
    # used to miss segments on this exact path.
    assert spies["cover"][0][0][0][0].text == "archived text"

    window.close()
    process_events()


def test_manual_edit_reaches_content_consumers_but_not_transcript_view(monkeypatch, process_events):
    """MANUAL_EDIT must not re-apply set_result() back into transcript_view
    — it's the source of the edit signal being handled."""
    from transcriber import Segment, TranscriptionResult
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    result = TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello world")],
        language="en",
        duration=1.0,
    )
    window._on_finished(result, open_record=True, save_history=False)
    process_events()

    spies = _consumer_spies(monkeypatch, window)
    transcript_view_calls = _spy(monkeypatch, window.transcript_view, "set_result")

    edited = TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello GAMMA")],
        language="en",
        duration=1.0,
    )
    monkeypatch.setattr(window.transcript_view, "get_result", lambda: edited)
    window._on_transcript_changed("replace_all")
    process_events()

    for name, calls in spies.items():
        assert len(calls) == 1, f"{name} consumer called {len(calls)} times, expected 1"
    assert transcript_view_calls == []

    window.close()
    process_events()
