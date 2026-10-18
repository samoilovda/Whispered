"""HistoryStore.query_records — the Library's paged, filtered query
(UI/UX audit 2026-10-18, UX-04 / T04)."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from core.history import HistoryStore, RecordQuery


def _result(text: str = "Hello world"):
    return SimpleNamespace(
        segments=[SimpleNamespace(start=0.0, end=5.0, text=text, speaker=None)],
        language="en", duration=5.0,
    )


@pytest.fixture
def store(tmp_path):
    return HistoryStore(db_path=tmp_path / "history.db")


def _bulk(store: HistoryStore, count: int, *, same_time: bool = False, **add_kwargs):
    ids = []
    for i in range(count):
        ids.append(store.add(_result(f"record number {i}"), source_path=f"/m/{i}.mp3",
                             **add_kwargs))
    if same_time:
        with sqlite3.connect(store._db_path) as conn:
            conn.execute("UPDATE transcripts SET created_at = '2026-10-01T10:00:00+00:00'")
    return ids


def _all_pages(store: HistoryStore, query: RecordQuery) -> list[int]:
    seen: list[int] = []
    offset = 0
    while True:
        page = store.query_records(RecordQuery(
            text=query.text, source_kind=query.source_kind, recipe=query.recipe,
            limit=query.limit, offset=offset,
        ))
        seen.extend(r.id for r in page.items)
        offset += len(page.items)
        if not page.has_more:
            return seen


def test_205th_record_is_reachable_with_a_next_page(store):
    ids = _bulk(store, 205)
    first = store.query_records(RecordQuery(limit=200))
    assert len(first.items) == 200 and first.has_more
    assert first.matched == first.total == 205
    rest = store.query_records(RecordQuery(limit=200, offset=200))
    assert [r.id for r in rest.items][-1] == ids[0]  # the oldest
    assert not rest.has_more


def test_pages_neither_repeat_nor_skip_when_timestamps_tie(store):
    ids = _bulk(store, 57, same_time=True)
    seen = _all_pages(store, RecordQuery(limit=10))
    assert seen == sorted(ids, reverse=True)


def test_filter_is_applied_before_the_limit(store):
    live_id = store.add(_result(), source_path="", source_kind="live",
                        source_name="Live transcript")
    _bulk(store, 250)  # newer file records push the live one past page 1
    page = store.query_records(RecordQuery(source_kind="live", limit=200))
    assert [r.id for r in page.items] == [live_id]
    assert page.matched == 1 and page.total == 251


def test_legacy_rows_classify_like_the_library(store):
    rec = store.add(_result(), source_path="/x/REC_001.wav", source_name="REC_001.wav")
    with sqlite3.connect(store._db_path) as conn:
        conn.execute("UPDATE transcripts SET source_kind = '' WHERE id = ?", (rec,))
    assert [r.id for r in store.query_records(RecordQuery(source_kind="recorder")).items] == [rec]


def test_recipe_filter_uses_the_latest_run(store):
    a, b = _bulk(store, 2)
    with sqlite3.connect(store._db_path) as conn:
        conn.executemany(
            "INSERT INTO job_runs (record_id, recipe, started_at) VALUES (?, ?, 'x')",
            [(a, "meeting_notes"), (a, "youtube_video"), (b, "meeting_notes")],
        )
    page = store.query_records(RecordQuery(recipe="meeting_notes"))
    assert [r.id for r in page.items] == [b]


def test_search_match_beyond_the_first_page_is_found(store):
    target = store.add(_result("a unique needle phrase"), source_path="/m/needle.mp3")
    _bulk(store, 230)
    page = store.query_records(RecordQuery(text="needle", limit=200))
    assert [r.id for r in page.items] == [target]
    assert page.matched == 1
    assert "**needle**" in page.items[0].preview  # FTS snippet for the page row


def test_search_ranks_title_matches_first_and_pages_stably(store):
    ids = _bulk(store, 30)
    store.set_title(ids[3], "Record about records")
    seen = _all_pages(store, RecordQuery(text="record", limit=7))
    assert seen[0] == ids[3]
    assert sorted(seen) == sorted(ids)
    assert len(seen) == len(set(seen))


def test_like_fallback_paginates_and_filters_too(store):
    store._fts_available = False
    _bulk(store, 12)
    live = store.add(_result("record live"), source_path="", source_kind="live")
    page = store.query_records(RecordQuery(text="record", source_kind="live"))
    assert [r.id for r in page.items] == [live]
    assert _all_pages(store, RecordQuery(text="record", limit=5)).__len__() == 13
