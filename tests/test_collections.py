"""Collections, saved searches and hit times in the history store (N03)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.history import HistoryStore, RecordQuery


@pytest.fixture
def store(tmp_path):
    return HistoryStore(db_path=tmp_path / "h.db")


def _add(store, *segments, **kw):
    result = SimpleNamespace(
        segments=[SimpleNamespace(start=s, end=s + 5, text=t, speaker=None) for s, t in segments],
        language="en", duration=60.0)
    return store.add(result, source_path=kw.pop("path", ""), **kw)


def test_a_record_can_be_in_several_collections_and_filtering_pages_correctly(store):
    ids = [_add(store, (0.0, f"record {i}")) for i in range(30)]
    a, b = store.create_collection("Interviews"), store.create_collection("Lectures")
    for rid in ids[:12]:
        store.set_record_in_collection(rid, a, True)
    for rid in ids[8:15]:
        store.set_record_in_collection(rid, b, True)
    assert sorted(store.collections_of(ids[10])) == sorted([a, b])
    page = store.query_records(RecordQuery(collection_id=a, limit=5))
    assert page.matched == 12 and page.has_more and len(page.items) == 5
    seen = []
    offset = 0
    while True:
        part = store.query_records(RecordQuery(collection_id=a, limit=5, offset=offset))
        seen += [r.id for r in part.items]
        offset += len(part.items)
        if not part.has_more:
            break
    assert sorted(seen) == sorted(ids[:12]) and len(seen) == len(set(seen))
    both = store.query_records(RecordQuery(collection_id=b, text="record 1", limit=50))
    assert {r.id for r in both.items} <= set(ids[8:15])           # filter + search together


def test_renaming_and_deleting_a_collection_never_deletes_records(store):
    rid = _add(store, (0.0, "words"))
    cid = store.create_collection("Temp")
    store.set_record_in_collection(rid, cid, True)
    assert store.rename_collection(cid, "Permanent") and store.list_collections()[0]["name"] == "Permanent"
    assert store.list_collections()[0]["records"] == 1
    store.delete_collection(cid)
    assert store.list_collections() == [] and store.get_record(rid) is not None
    assert store.collections_of(rid) == []
    store.set_record_in_collection(rid, cid, True)               # a stale id is harmless
    assert store.query_records(RecordQuery()).matched == 1


def test_names_are_unique_ignoring_case_and_never_empty(store):
    assert store.create_collection("Work")
    assert store.create_collection("work") is None and store.create_collection("  ") is None
    other = store.create_collection("Home")
    assert not store.rename_collection(other, "WORK") and not store.rename_collection(other, "")


def test_deleting_a_record_removes_only_its_membership(store):
    a, b = _add(store, (0.0, "x")), _add(store, (0.0, "y"))
    cid = store.create_collection("C")
    store.set_record_in_collection(a, cid, True)
    store.set_record_in_collection(b, cid, True)
    store.delete(a)
    assert store.list_collections()[0]["records"] == 1


def test_saved_searches_round_trip_replace_and_delete(store):
    first = store.save_search("Podcasts", {"text": "guest", "scope": "all", "collection": 0})
    store.save_search("podcasts", {"text": "host"})            # same name (any case) replaces
    searches = store.list_saved_searches()
    assert [s["query"]["text"] for s in searches] == ["host"] and first != searches[0]["id"]
    assert store.save_search("", {}) is None
    store.delete_saved_search(searches[0]["id"])
    assert store.list_saved_searches() == []


def test_hit_time_points_at_where_the_words_are_said_and_follows_edits(store):
    rid = _add(store, (0.0, "welcome everyone"), (12.0, "the budget is next quarter"),
               (30.0, "budget again, then the quarter"))
    assert store.match_time(rid, "budget quarter") == 12.0      # first segment with all the words
    assert store.match_time(rid, "welcome budget") == 0.0       # else the first with any
    assert store.match_time(rid, "zebra") is None and store.match_time(rid, "  ") is None
    assert store.match_time(9999, "budget") is None
    result = SimpleNamespace(segments=[SimpleNamespace(start=0.0, end=5.0, text="welcome everyone", speaker=None),
                                       SimpleNamespace(start=12.0, end=17.0, text="the plan is next quarter", speaker=None)],
                             language="en", duration=60.0)
    store.update_result(rid, result, {})
    assert store.match_time(rid, "budget") is None             # the edited-away match is gone
    assert rid not in [r.id for r in store.query_records(RecordQuery(text="budget")).items]
