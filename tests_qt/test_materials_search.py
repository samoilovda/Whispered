"""Real-Qt tests for B7 (docs/IMPROVEMENT_PLAN_2026-08.ru.md): search
across generated materials — the Library's scope toggle, the Ctrl+K
palette searching both scopes, and MainWindow routing a materials hit to
the tab that generated it.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton

from core.history import HistoryStore
from core.i18n import load_locale, tr
from transcriber import Segment, TranscriptionResult
from ui.command_palette import CommandPalette
from ui.library_view import LibraryView


def _make_store(tmp_path):
    return HistoryStore(db_path=tmp_path / "history.sqlite3")


def _add_record(store, name: str) -> int:
    result = TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello world")], language="en", duration=1.0,
    )
    return store.add(result, source_path="", model="", source_name=name)


def _item_widget_at(view: LibraryView, row: int):
    return view._list.itemWidget(view._list.item(row))


# ------------------------------------------------------------------ LibraryView scope toggle

def test_scope_toggle_chips_exist_for_all_three_scopes(process_events):
    load_locale("en")
    view = LibraryView()
    process_events()

    labels = {
        button.text() for button in view.findChildren(QPushButton)
        if button.property("role") == "quick-chip"
    }
    for key in ("all", "transcripts", "materials"):
        assert tr(f"library_search_scope_{key}") in labels

    view.close()


def test_materials_scope_finds_a_marker_word_in_an_article(
    monkeypatch, tmp_path, process_events,
):
    """B7 acceptance criterion: an article with a marker word is found
    under the "Materials" scope."""
    load_locale("en")
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = _add_record(store, "podcast.mp3")
    store.set_artifact_text(record_id, "article", "/out/article.md", "an article about zorblaxian pricing")

    view = LibraryView()
    view._set_search_scope("materials")
    process_events()

    view._search_edit.setText("zorblaxian")
    view._run_search()
    process_events()

    assert len(view._material_hits) == 1
    assert view._material_hits[0].record_id == record_id
    assert view._material_hits[0].type == "article"
    assert view._list.count() == 1

    view.close()


def test_transcripts_scope_does_not_show_material_hits(
    monkeypatch, tmp_path, process_events,
):
    load_locale("en")
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = _add_record(store, "podcast.mp3")
    store.set_artifact_text(record_id, "article", "/out/article.md", "about zorblaxian pricing")

    view = LibraryView()
    view._set_search_scope("transcripts")
    process_events()

    view._search_edit.setText("zorblaxian")
    view._run_search()
    process_events()

    assert view._material_hits == []

    view.close()


def test_materials_scope_with_empty_query_shows_nothing(
    monkeypatch, tmp_path, process_events,
):
    """search_artifacts("") returns [] — there is no "browse all
    materials" view, unlike the transcript list."""
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    _add_record(store, "podcast.mp3")

    view = LibraryView()
    view._set_search_scope("materials")
    process_events()

    assert view._records == []
    assert view._material_hits == []
    assert view._list.count() == 0

    view.close()


def test_opening_a_material_hit_emits_open_record_with_its_type(
    monkeypatch, tmp_path, process_events,
):
    """B7 acceptance criterion: clicking a materials hit opens the record
    carrying the type that led to it."""
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = _add_record(store, "podcast.mp3")
    store.set_artifact_text(record_id, "insights", "/out/insights.md", "key insight about zorblaxian growth")

    view = LibraryView()
    view._set_search_scope("materials")
    view._search_edit.setText("zorblaxian")
    view._run_search()
    process_events()

    seen = []
    view.open_record.connect(lambda rid, atype: seen.append((rid, atype)))

    widget = _item_widget_at(view, 0)
    widget.open_button.click()
    process_events()

    assert seen == [(record_id, "insights")]

    view.close()


def test_opening_a_transcript_still_emits_an_empty_type(
    monkeypatch, tmp_path, process_events,
):
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    record_id = _add_record(store, "podcast.mp3")

    view = LibraryView()
    view.refresh()
    process_events()

    seen = []
    view.open_record.connect(lambda rid, atype: seen.append((rid, atype)))
    widget = _item_widget_at(view, 0)
    widget.open_button.click()
    process_events()

    assert seen == [(record_id, "")]

    view.close()


# ------------------------------------------------------------------ CommandPalette

def test_palette_lists_a_material_hit_and_emits_its_type(
    monkeypatch, tmp_path, process_events,
):
    load_locale("en")
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    record_id = _add_record(store, "podcast.mp3")
    store.set_artifact_text(record_id, "book", "/out/book.md", "a chapter about zorblaxian trade")

    palette = CommandPalette()
    palette.open_palette()
    palette.search.setText("zorblaxian")
    process_events()

    expected_label = tr("command_material", type=tr("library_chip_book"), name="podcast.mp3")
    labels = [palette.results.item(i).text() for i in range(palette.results.count())]
    assert expected_label in labels

    seen = []
    palette.record_requested.connect(lambda rid, atype: seen.append((rid, atype)))
    # Select the material row explicitly (index unknown among mixed
    # results) rather than assuming row 0.
    match_row = next(
        i for i in range(palette.results.count())
        if palette.results.item(i).data(Qt.ItemDataRole.UserRole)[0] == "material"
    )
    palette.results.setCurrentRow(match_row)
    palette._activate_current()
    process_events()

    assert seen == [(record_id, "book")]

    palette.close()


def test_palette_empty_query_does_not_call_search_artifacts(
    monkeypatch, tmp_path, process_events,
):
    store = _make_store(tmp_path)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    _add_record(store, "podcast.mp3")

    calls = []
    real_search_artifacts = store.search_artifacts

    def _tracked(*args, **kwargs):
        calls.append(args)
        return real_search_artifacts(*args, **kwargs)

    monkeypatch.setattr(store, "search_artifacts", _tracked)

    palette = CommandPalette()
    palette.open_palette()
    process_events()

    assert calls == []

    palette.close()


# ------------------------------------------------------------------ MainWindow tab routing

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


def test_open_record_view_with_no_type_lands_on_the_transcript_tab(
    window, process_events,
):
    from core.history import get_history_store

    store = get_history_store()
    record_id = _add_record(store, "podcast.mp3")

    window._open_record_view(record_id, "")
    process_events()

    assert window.main_tabs.currentWidget() is window.transcript_view


@pytest.mark.parametrize(
    "artifact_type, tab_attr",
    [
        ("article", "article_view"),
        ("insights", "insights_panel"),
        ("youtube", "youtube_panel"),
        ("book", "book_panel"),
    ],
)
def test_open_record_view_routes_to_the_matching_tab(
    window, process_events, artifact_type, tab_attr,
):
    """B7 acceptance criterion: a materials search hit's click opens the
    record on the tab that generated it — the reverse of
    MainWindow._STEP_TO_ARTIFACT_TYPE."""
    from core.history import get_history_store

    store = get_history_store()
    record_id = _add_record(store, "podcast.mp3")

    window._open_record_view(record_id, artifact_type)
    process_events()

    assert window.main_tabs.currentWidget() is getattr(window, tab_attr)
