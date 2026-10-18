"""Collections and saved searches in the Library (N03)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt

from core.history import HistoryStore
from core.i18n import load_locale, tr
from ui.library_view import LibraryView


def _add(store, text, title):
    result = SimpleNamespace(
        segments=[SimpleNamespace(start=0.0, end=5.0, text="intro", speaker=None),
                  SimpleNamespace(start=90.0, end=95.0, text=text, speaker=None)],
        language="en", duration=100.0)
    rid = store.add(result, source_path="", source_name=title)
    return rid


@pytest.fixture
def lib(monkeypatch, tmp_path, process_events):
    load_locale("en")
    store = HistoryStore(db_path=tmp_path / "h.db")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    a = _add(store, "we discuss the budget here", "a.mp3")
    b = _add(store, "nothing relevant", "b.mp3")
    view = LibraryView()
    view.refresh()
    process_events()
    return view, store, a, b


def _ids(view):
    return [view._list.item(r).data(Qt.ItemDataRole.UserRole) for r in range(view._list.count())
            if view._list.item(r).data(Qt.ItemDataRole.UserRole) is not None]


def test_collection_filter_and_chip(lib, process_events):
    view, store, a, b = lib
    cid = store.create_collection("Budget talks")
    view.set_membership(a, cid, True)
    view._set_collection(cid)
    assert _ids(view) == [a]
    assert any(kind == "collection" and text == "Budget talks" for kind, text in view._active_filter_chips())
    view._clear_one_filter("collection")
    assert sorted(_ids(view)) == sorted([a, b])
    view._set_collection(cid)
    view._reset_filters()
    assert view._active_collection == 0 and sorted(_ids(view)) == sorted([a, b])


def test_deleting_the_active_collection_does_not_leave_an_empty_library(lib, process_events, monkeypatch):
    view, store, a, b = lib
    cid = store.create_collection("Gone")
    view.set_membership(a, cid, True)
    view._set_collection(cid)
    store.delete_collection(cid)
    monkeypatch.setattr("ui.collections_dialog.CollectionsDialog.exec", lambda self: 0)
    view.manage_collections()
    assert view._active_collection == 0 and sorted(_ids(view)) == sorted([a, b])


def test_search_hit_shows_when_it_was_said_and_opens_there(lib, process_events):
    view, store, a, b = lib
    opened, opened_at = [], []
    view.open_record.connect(lambda rid, kind: opened.append(rid))
    view.open_record_at.connect(lambda rid, s: opened_at.append((rid, s)))
    view._search_edit.setText("budget")
    view._load("budget")
    assert _ids(view) == [a]
    widget = view._list.itemWidget(view._list.item(0))
    from PyQt6.QtWidgets import QLabel
    assert tr("library_hit_at", time="01:30") in " ".join(label.text() for label in widget.findChildren(QLabel))
    view._activate_item(view._list.item(0))
    assert opened_at == [(a, 90.0)] and opened == []


def test_a_saved_search_restores_text_scope_filters_and_collection(lib, process_events):
    view, store, a, b = lib
    cid = store.create_collection("C")
    view.set_membership(a, cid, True)
    view._set_collection(cid)
    view._set_filter("file")
    view._search_edit.setText("budget")
    store.save_search("Budget in C", view.current_query())
    view._reset_filters()
    assert sorted(_ids(view)) == sorted([a, b])
    saved = store.list_saved_searches()[0]["query"]
    view.apply_query(saved)
    assert view._search_edit.text() == "budget" and view._active_collection == cid
    assert _ids(view) == [a]
    view._fill_saved_menu()
    assert [x.text() for x in view._saved_menu.actions()][0] == "Budget in C"
