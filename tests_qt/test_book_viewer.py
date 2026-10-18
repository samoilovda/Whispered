"""The book has its own place (UI/UX audit 2026-10-18, UX-08 / T08).

Before: a finished book overwrote the cleaned-text tab, was not restored
when the record was reopened, and a search hit on a book opened a tab
showing only the run settings.
"""

from __future__ import annotations

import pytest

from core.history import HistoryStore
from core.i18n import load_locale, tr
from transcriber import Segment, TranscriptionResult


class _FakeBookPipeline:
    calls = 0

    def __init__(self, *_a, **_k):
        pass

    def process(self, transcript_text, source_path, **kwargs):
        from book_pipeline import BookResult, BookStageResult

        _FakeBookPipeline.calls += 1
        return BookResult(source_path=source_path, stages=[BookStageResult(
            stage="unwrap", output_text="# Chapter\n\nThe book body.",
            output_path=str(kwargs.get("output_dir", ".")) + "/book_unwrap.md",
        )])


def _result(text="hello world"):
    return TranscriptionResult(segments=[Segment(0.0, 2.0, text)], language="en", duration=2.0)


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
    monkeypatch.setattr("book_pipeline.BookPipeline", _FakeBookPipeline)
    _FakeBookPipeline.calls = 0
    record = store.add(_result(), source_path="", source_name="talk.mp3")
    windows = []

    def make():
        win = MainWindow()
        win.show()
        process_events()
        windows.append(win)
        return win

    yield make, store, record
    for win in windows:
        win._revision_save_timer.stop()
        win.close()
    process_events()


def _run_book(window, process_events):
    window.book_panel.run_single_requested.emit(True, False, "")
    runner = window._book_job
    assert runner is not None and runner.wait(3000)
    process_events()


def test_fresh_book_shows_in_its_tab_and_reopens_without_a_model(setup, process_events):
    make, store, record = setup
    window = make()
    window._open_record_view(record)
    _run_book(window, process_events)
    panel = window.book_panel
    assert "The book body." in panel.book_text()
    assert window.main_tabs.currentWidget() is panel
    assert not window.cleaned_view.has_content()
    assert panel.book_state_label.text() == tr("book_state_current")
    calls = _FakeBookPipeline.calls

    fresh = make()
    fresh._open_record_view(record)
    assert fresh.book_panel.book_text() == panel.book_text()
    assert _FakeBookPipeline.calls == calls  # restored from book.md, no run


def test_other_record_does_not_show_this_book(setup, process_events):
    make, store, record = setup
    other = store.add(_result("another"), source_path="", source_name="other.mp3")
    window = make()
    window._open_record_view(record)
    _run_book(window, process_events)
    window._open_record_view(other)
    assert not window.book_panel.has_book()


def test_missing_book_file_is_a_recoverable_state(setup, process_events):
    make, store, record = setup
    window = make()
    window._open_record_view(record)
    _run_book(window, process_events)
    store.set_artifacts(record, ["transcript", "book"])
    from core.paths import artifact_dir

    path = next(artifact_dir(record, "recording").parent.rglob("book.md"), None) \
        or next(artifact_dir(record, "talk.mp3").parent.rglob("book.md"))
    path.unlink()

    fresh = make()
    fresh._open_record_view(record)
    assert not fresh.book_panel.has_book()
    assert not fresh.book_panel.book_missing_label.isHidden()


def test_edit_marks_the_book_stale(setup, process_events):
    make, store, record = setup
    window = make()
    window._open_record_view(record)
    _run_book(window, process_events)
    result = window.transcript_view.get_result()
    result.segments[0].text = "edited words"
    window.transcript_view.result_changed.emit("text")

    fresh = make()
    fresh._open_record_view(record)
    assert fresh.book_panel.book_state_label.text() == tr("book_state_stale")


def test_search_hit_on_a_book_opens_the_book_text(setup, process_events):
    make, store, record = setup
    window = make()
    window._open_record_view(record)
    _run_book(window, process_events)
    fresh = make()
    fresh._open_record_view(record, "book")
    assert fresh.main_tabs.currentWidget() is fresh.book_panel
    assert fresh.book_panel.has_book()
