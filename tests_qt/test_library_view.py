"""Real-Qt tests for ui/library_view.py's B8 additions: a record card's
run composition (failed-step badges) and the recipe filter (see
docs/UI_REDESIGN_PLAN_2026-09.ru.md, B8).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton

from application.job_engine import JobRun
from application.run_store import save_run
from core.history import HistoryStore
from core.i18n import load_locale
from domain.job import JobSpec, StepOutcome, StepSpec, StepStatus
from transcriber import Segment, TranscriptionResult
from ui.library_view import LibraryView


def _make_store(tmp_path):
    return HistoryStore(db_path=tmp_path / "history.sqlite3")


def _add_record(store, name: str) -> int:
    result = TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello world")], language="en", duration=1.0,
    )
    return store.add(result, source_path="", model="", source_name=name)


def _save_run(record_id: int, recipe: str, outcomes: dict) -> None:
    run = JobRun(spec=JobSpec(name=recipe, steps=tuple(StepSpec(n) for n in outcomes)))
    run.outcomes.update(outcomes)
    save_run(record_id, recipe, run, status="done")


def _item_widget(view: LibraryView, record_id: int):
    for row in range(view._list.count()):
        item = view._list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == record_id:
            return view._list.itemWidget(item)
    return None


def _error_badge_texts(widget) -> "list[str]":
    return [
        label.text() for label in widget.findChildren(QLabel)
        if label.property("role") == "badge-pill-error"
    ]


def test_failed_step_badge_shown_without_opening_the_record(monkeypatch, tmp_path, process_events):
    load_locale("en")
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = _add_record(store, "lecture.mp3")
    _save_run(record_id, "youtube_video", {
        "transcribe": StepOutcome("transcribe", StepStatus.SUCCEEDED),
        "cover": StepOutcome("cover", StepStatus.FAILED, error="boom"),
    })

    view = LibraryView()
    view.refresh()
    process_events()

    widget = _item_widget(view, record_id)
    assert widget is not None
    assert any("Cover" in text for text in _error_badge_texts(widget))

    view.close()


def test_succeeded_run_shows_no_error_badges(monkeypatch, tmp_path, process_events):
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = _add_record(store, "clean.mp3")
    _save_run(record_id, "transcript_only", {
        "transcribe": StepOutcome("transcribe", StepStatus.SUCCEEDED),
    })

    view = LibraryView()
    view.refresh()
    process_events()

    widget = _item_widget(view, record_id)
    assert widget is not None
    assert _error_badge_texts(widget) == []

    view.close()


def test_record_with_no_run_at_all_is_not_a_crash(monkeypatch, tmp_path, process_events):
    """Pre-B3 history / a record no recipe ever ran against — load_latest_run
    returns None; the card must still render."""
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = _add_record(store, "orphan.mp3")

    view = LibraryView()
    view.refresh()
    process_events()

    widget = _item_widget(view, record_id)
    assert widget is not None
    assert _error_badge_texts(widget) == []

    view.close()


def test_recipe_filter_shows_only_matching_records(monkeypatch, tmp_path, process_events):
    load_locale("en")
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    yt_id = _add_record(store, "video.mp4")
    _save_run(yt_id, "youtube_video", {
        "transcribe": StepOutcome("transcribe", StepStatus.SUCCEEDED),
    })
    book_id = _add_record(store, "novel.mp3")
    _save_run(book_id, "book", {
        "transcribe": StepOutcome("transcribe", StepStatus.SUCCEEDED),
    })

    view = LibraryView()
    view.refresh()
    process_events()
    assert _item_widget(view, yt_id) is not None
    assert _item_widget(view, book_id) is not None

    view._set_recipe_filter("youtube_video")
    process_events()

    assert _item_widget(view, yt_id) is not None
    assert _item_widget(view, book_id) is None

    view.close()


def test_one_refresh_reads_runs_in_a_single_query(monkeypatch, tmp_path, process_events):
    """Regression: reading each card's run with its own load_latest_run()
    opened one SQLite connection per record, on the GUI thread, for every
    refresh / filter click / debounced search keystroke."""
    import sqlite3

    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    for index in range(40):
        _add_record(store, f"rec{index}.mp3")

    view = LibraryView()
    view.refresh()
    process_events()

    connections = []
    real_connect = sqlite3.connect
    monkeypatch.setattr(
        sqlite3, "connect",
        lambda *a, **k: connections.append(1) or real_connect(*a, **k),
    )
    view.refresh()
    process_events()

    assert len(view._records) == 40
    # one for HistoryStore.list(), one for the batched job_runs lookup
    assert len(connections) <= 2, f"{len(connections)} connections for 40 records"

    view.close()


def test_recipe_filter_chips_exist_for_every_builtin_recipe(process_events):
    view = LibraryView()
    process_events()

    labels = {
        button.text() for button in view.findChildren(QPushButton)
        if button.property("role") == "quick-chip"
    }
    from core.i18n import tr
    from domain.recipe import BUILTIN_RECIPES

    for recipe in BUILTIN_RECIPES:
        assert tr(f"recipe_{recipe.builtin_key}") in labels

    view.close()


def test_both_filter_rows_are_labeled(process_events):
    """Regression: two independent chip rows (source, recipe) each
    defaulted to an unlabeled "All" chip — indistinguishable from each
    other (see docs/IMPROVEMENT_PLAN_2026-08.ru.md, A2)."""
    load_locale("en")
    from core.i18n import tr

    view = LibraryView()
    process_events()

    texts = [label.text() for label in view.findChildren(QLabel)]
    assert tr("library_filter_source_label") in texts
    assert tr("library_filter_recipe_label") in texts

    view.close()


def test_reset_button_appears_only_with_a_non_default_filter_or_search(
    monkeypatch, tmp_path, process_events,
):
    load_locale("en")
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    _add_record(store, "one.mp3")

    view = LibraryView()
    view.refresh()
    process_events()
    assert not view._reset_filters_btn.isVisibleTo(view)

    view._set_filter("file")
    process_events()
    assert view._reset_filters_btn.isVisibleTo(view)

    view._set_filter("all")
    process_events()
    assert not view._reset_filters_btn.isVisibleTo(view)

    view._set_recipe_filter("book")
    process_events()
    assert view._reset_filters_btn.isVisibleTo(view)
    view._set_recipe_filter("all")
    process_events()

    view._search_edit.setText("one")
    view._run_search()
    process_events()
    assert view._reset_filters_btn.isVisibleTo(view)

    view.close()


def test_reset_button_clears_both_filters_and_the_search_text(
    monkeypatch, tmp_path, process_events,
):
    load_locale("en")
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    yt_id = _add_record(store, "video.mp4")
    _save_run(yt_id, "youtube_video", {
        "transcribe": StepOutcome("transcribe", StepStatus.SUCCEEDED),
    })
    book_id = _add_record(store, "novel.mp3")
    _save_run(book_id, "book", {
        "transcribe": StepOutcome("transcribe", StepStatus.SUCCEEDED),
    })

    view = LibraryView()
    view.refresh()
    process_events()

    view._set_recipe_filter("youtube_video")
    view._search_edit.setText("video")
    view._run_search()
    process_events()
    assert _item_widget(view, book_id) is None
    assert view._reset_filters_btn.isVisibleTo(view)

    view._reset_filters_btn.click()
    process_events()

    assert view._active_filter == "all"
    assert view._active_recipe_filter == "all"
    assert view._search_edit.text() == ""
    assert view._filter_all_btn.isChecked()
    assert view._recipe_filter_all_btn.isChecked()
    assert _item_widget(view, yt_id) is not None
    assert _item_widget(view, book_id) is not None
    assert not view._reset_filters_btn.isVisibleTo(view)

    view.close()
