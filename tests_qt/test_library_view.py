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


def test_resume_button_shown_for_a_failed_run_with_unfinished_steps(
    monkeypatch, tmp_path, process_events,
):
    """B2, docs/IMPROVEMENT_PLAN_2026-08.ru.md: a "Продолжить" button
    appears on a card whose latest run is failed/interrupted with a step
    that isn't SUCCEEDED/SKIPPED."""
    load_locale("en")
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = _add_record(store, "podcast.mp3")
    run = JobRun(spec=JobSpec(name="podcast_article", steps=(
        StepSpec("transcribe"), StepSpec("clean"), StepSpec("article"),
    )))
    run.outcomes["transcribe"] = StepOutcome("transcribe", StepStatus.SUCCEEDED)
    run.outcomes["clean"] = StepOutcome("clean", StepStatus.SUCCEEDED)
    run.outcomes["article"] = StepOutcome("article", StepStatus.FAILED, error="boom")
    save_run(record_id, "podcast_article", run, status="failed")

    view = LibraryView()
    view.refresh()
    process_events()

    from core.i18n import tr

    widget = _item_widget(view, record_id)
    assert widget is not None
    assert hasattr(widget, "resume_button")
    assert widget.resume_button.text() == tr("library_resume_run")

    view.close()


def test_no_resume_button_for_a_fully_succeeded_run(monkeypatch, tmp_path, process_events):
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
    assert not hasattr(widget, "resume_button")

    view.close()


def test_no_resume_button_while_the_run_is_still_running(monkeypatch, tmp_path, process_events):
    """A row genuinely still 'running' (this process's own live run, not
    a dead one — see run_store.mark_stale_running_as_interrupted) must
    not offer to resume something already in progress."""
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = _add_record(store, "in-progress.mp3")
    run = JobRun(spec=JobSpec(name="podcast_article", steps=(
        StepSpec("transcribe"), StepSpec("clean"),
    )))
    run.outcomes["transcribe"] = StepOutcome("transcribe", StepStatus.SUCCEEDED)
    save_run(record_id, "podcast_article", run, status="running")

    view = LibraryView()
    view.refresh()
    process_events()

    widget = _item_widget(view, record_id)
    assert widget is not None
    assert not hasattr(widget, "resume_button")

    view.close()


def test_clicking_resume_emits_resume_run_with_the_record_id(
    monkeypatch, tmp_path, process_events,
):
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = _add_record(store, "podcast.mp3")
    run = JobRun(spec=JobSpec(name="podcast_article", steps=(
        StepSpec("transcribe"), StepSpec("article"),
    )))
    run.outcomes["transcribe"] = StepOutcome("transcribe", StepStatus.SUCCEEDED)
    run.outcomes["article"] = StepOutcome("article", StepStatus.FAILED, error="boom")
    save_run(record_id, "podcast_article", run, status="failed")

    view = LibraryView()
    view.refresh()
    process_events()

    seen = []
    view.resume_run.connect(seen.append)
    widget = _item_widget(view, record_id)
    widget.resume_button.click()
    process_events()

    assert seen == [record_id]

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

    labels = {action.text() for action in view._filter_menu.actions()}
    from core.i18n import tr
    from domain.recipe import BUILTIN_RECIPES

    for recipe in BUILTIN_RECIPES:
        assert tr(f"recipe_{recipe.builtin_key}") in labels

    view.close()


def test_refresh_recipe_filters_adds_and_removes_custom_chips(
    monkeypatch, tmp_path, process_events,
):
    """B4, docs/IMPROVEMENT_PLAN_2026-08.ru.md: the recipe filter row
    grows a chip for every Config.recipes entry, not just the five
    built-ins, and drops it again once the recipe is gone.

    Asserts against _recipe_filter_buttons/_recipe_filter_group (updated
    synchronously by _build_recipe_filter_chips()) rather than
    findChildren() — a torn-down chip's widget.deleteLater() only
    actually destroys it on a later real event-loop turn, which
    processEvents() doesn't reliably drive under the offscreen QPA this
    suite runs under.
    """
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    cfg = config.Config()
    monkeypatch.setattr(config, "_config", cfg)

    view = LibraryView()
    process_events()

    assert "My custom" not in view._recipe_filter_actions

    cfg.recipes = [{"name": "My custom", "steps": ["transcribe", "clean"], "builtin_key": ""}]
    view.refresh_recipe_filters()
    process_events()
    assert "My custom" in view._recipe_filter_actions
    added = view._recipe_filter_actions["My custom"]
    assert added.text() == "My custom"
    assert added in view._recipe_filter_group.actions()

    cfg.recipes = []
    view.refresh_recipe_filters()
    process_events()
    assert "My custom" not in view._recipe_filter_actions
    assert added not in view._recipe_filter_group.actions()

    view.close()


def test_refresh_recipe_filters_falls_back_to_all_once_active_recipe_is_gone(
    monkeypatch, tmp_path, process_events,
):
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    cfg = config.Config(
        recipes=[{"name": "Temp", "steps": ["transcribe"], "builtin_key": ""}]
    )
    monkeypatch.setattr(config, "_config", cfg)

    view = LibraryView()
    process_events()
    view._set_recipe_filter("Temp")
    assert view._active_recipe_filter == "Temp"

    cfg.recipes = []
    view.refresh_recipe_filters()
    process_events()

    assert view._active_recipe_filter == "all"
    assert view._recipe_filter_all_action.isChecked()

    view.close()


def test_every_filter_group_is_labeled(process_events):
    """Regression: independent filter groups (scope, source, recipe) each
    default to an unlabeled "All" — indistinguishable from each other
    without a heading (see docs/IMPROVEMENT_PLAN_2026-08.ru.md, A2)."""
    load_locale("en")
    from core.i18n import tr

    view = LibraryView()
    process_events()

    sections = [a.text() for a in view._filter_menu.actions() if a.isSeparator()]
    assert tr("library_search_scope_section") in sections
    assert tr("library_filter_source_label") in sections
    assert tr("library_filter_recipe_label") in sections

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
    assert view._filter_all_action.isChecked()
    assert view._recipe_filter_all_action.isChecked()
    assert _item_widget(view, yt_id) is not None
    assert _item_widget(view, book_id) is not None
    assert not view._reset_filters_btn.isVisibleTo(view)

    view.close()


def _record_rows(view):
    return [
        view._list.item(row) for row in range(view._list.count())
        if view._list.item(row).data(Qt.ItemDataRole.UserRole) is not None
    ]


def test_browsing_groups_records_under_date_headers(monkeypatch, tmp_path, process_events):
    load_locale("en")
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    _add_record(store, "one.mp3")

    view = LibraryView()
    view.refresh()
    process_events()

    headers = [
        view._list.itemWidget(view._list.item(row)).text()
        for row in range(view._list.count())
        if view._list.item(row).data(Qt.ItemDataRole.UserRole) is None
    ]
    assert headers == ["Today"]
    # The media extension is dropped from the shown name.
    widget = view._list.itemWidget(_record_rows(view)[0])
    assert widget.title_label.full_text() == "one"

    view._search_edit.setText("hello")
    view._run_search()
    process_events()
    assert len(_record_rows(view)) == view._list.count()  # no headers in results

    view.close()


def test_a_single_click_opens_the_record(monkeypatch, tmp_path, process_events):
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    record_id = _add_record(store, "one.mp3")

    view = LibraryView()
    view.refresh()
    process_events()
    opened = []
    view.open_record.connect(lambda rid, kind: opened.append((rid, kind)))

    view._list.itemClicked.emit(_record_rows(view)[0])
    assert opened == [(record_id, "")]

    view.close()


def test_active_filters_show_as_removable_chips(monkeypatch, tmp_path, process_events):
    load_locale("en")
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    _add_record(store, "one.mp3")

    view = LibraryView()
    view.refresh()
    process_events()
    assert view._filter_btn.text() == ""

    view._set_filter("recorder")
    process_events()
    chips = [
        b for b in view._active_filters.findChildren(QPushButton)
        if b.property("role") == "filter-chip" and not b.isHidden()
    ]
    assert [c.text().split()[0] for c in chips] == ["Recorder"]
    assert view._filter_btn.text() == "1"

    chips[0].click()
    process_events()
    assert view._active_filter == "all"
    assert view._filter_all_action.isChecked()
    assert view._filter_btn.text() == ""

    view.close()


def test_record_count_uses_plural_forms(monkeypatch, tmp_path, process_events):
    load_locale("ru")
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    for name in ("a.mp3", "b.mp3"):
        _add_record(store, name)

    view = LibraryView()
    view.refresh()
    process_events()
    assert view._status.text() == "2 записи"

    view.close()
    load_locale("en")


def test_renaming_a_record_shows_the_new_name(monkeypatch, tmp_path, process_events):
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    record_id = _add_record(store, "audio123.m4a")
    monkeypatch.setattr(
        "ui.library_view.QInputDialog.getText", lambda *a, **k: ("Interview", True)
    )

    view = LibraryView()
    view.refresh()
    process_events()
    renamed = []
    view.record_renamed.connect(lambda rid, title: renamed.append((rid, title)))

    view.rename_record(record_id)
    process_events()
    assert renamed == [(record_id, "Interview")]
    widget = view._list.itemWidget(_record_rows(view)[0])
    assert widget.title_label.full_text() == "Interview"

    view.close()


def test_a_record_with_a_run_in_progress_says_so(monkeypatch, tmp_path, process_events):
    load_locale("en")
    from core.i18n import tr

    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    record_id = _add_record(store, "talk.mp3")
    run = JobRun(spec=JobSpec(name="youtube_video", steps=(StepSpec("transcribe"),)))
    run.outcomes["transcribe"] = StepOutcome("transcribe", StepStatus.SUCCEEDED)
    save_run(record_id, "youtube_video", run, status="running")

    view = LibraryView()
    view.refresh()
    process_events()
    widget = _item_widget(view, record_id)
    assert widget.running_label.text() == f"◌ {tr('library_running')}"
    view.close()


def test_rename_suggests_the_youtube_title(monkeypatch, tmp_path, process_events):
    import json

    from core.paths import artifact_dir

    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    result = TranscriptionResult(segments=[Segment(0.0, 1.0, "x")], language="en", duration=1.0)
    record_id = store.add(result, source_path="/media/audio123.m4a", model="")
    folder = artifact_dir(record_id, "/media/audio123.m4a")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "youtube_package.json").write_text(
        json.dumps({"yt_titles": ["How to find your footing"]}), encoding="utf-8",
    )
    offered = []

    def _get_text(_parent, _title, _label, _mode, default):
        offered.append(default)
        return default, True

    monkeypatch.setattr("ui.library_view.QInputDialog.getText", _get_text)
    view = LibraryView()
    view.refresh()
    view.rename_record(record_id)
    assert offered == ["How to find your footing"]
    assert store.get_title(record_id) == "How to find your footing"
    view.close()


def test_search_snippets_lose_the_json_and_bold_the_match():
    from ui.library_view import _clean_snippet, _snippet_html

    raw = '…355.62, "text": "**Ресурсы** могут быть", "speaker": null, "end…'
    cleaned = _clean_snippet(raw)
    assert cleaned == "… **Ресурсы** могут быть"
    assert _snippet_html(cleaned) == "… <b>Ресурсы</b> могут быть"
    assert _clean_snippet('end": 12.5, "text": "budget <ok>') == "budget <ok>"
    assert _snippet_html("budget <ok>") == "budget &lt;ok&gt;"
