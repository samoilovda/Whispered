"""Tests for core/history.py — no Qt or network required."""

# Qt and core.lm_client/core.ai_worker stand-ins come from tests/conftest.py.
import os
import sqlite3
import stat

import pytest
from types import SimpleNamespace

from core.history import _MIGRATIONS, HistoryStore, _fts_query


def _make_result(segments=None, language="en", duration=60.0):
    if segments is None:
        segments = [
            SimpleNamespace(start=0.0, end=5.0, text="Hello world", speaker="Speaker 1"),
            SimpleNamespace(start=5.0, end=10.0, text="How are you", speaker="Speaker 2"),
        ]
    return SimpleNamespace(segments=segments, language=language, duration=duration)


@pytest.fixture
def store(tmp_path):
    return HistoryStore(db_path=tmp_path / "test_history.db")


class TestHistoryStoreBasics:
    @pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions only")
    def test_database_is_owner_only(self, tmp_path):
        path = tmp_path / "private-history.db"
        HistoryStore(db_path=path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_empty_list(self, store):
        assert store.list() == []

    def test_add_and_list(self, store):
        result = _make_result()
        rid = store.add(result, "/tmp/audio.wav", model="large-v3")
        assert isinstance(rid, int)
        records = store.list()
        assert len(records) == 1
        assert records[0].id == rid
        assert records[0].source_name == "audio.wav"
        assert records[0].language == "en"
        assert abs(records[0].duration - 60.0) < 1e-6
        assert records[0].model == "large-v3"

    def test_add_transcript_only_live_record(self, store):
        rid = store.add(
            _make_result(),
            "",
            source_name="Live 2026-07-27 14:30",
            source_kind="live",
        )
        record = store.get_record(rid)
        assert record["source_path"] == ""
        assert record["source_name"] == "Live 2026-07-27 14:30"
        assert record["source_kind"] == "live"

    def test_get_payload(self, store):
        result = _make_result()
        rid = store.add(result, "/tmp/audio.wav")
        payload = store.get(rid)
        assert payload is not None
        assert payload["language"] == "en"
        assert len(payload["segments"]) == 2
        assert payload["segments"][0]["text"] == "Hello world"

    def test_get_nonexistent(self, store):
        assert store.get(9999) is None

    def test_delete(self, store):
        result = _make_result()
        rid = store.add(result, "/tmp/audio.wav")
        assert store.count() == 1
        deleted = store.delete(rid)
        assert deleted is True
        assert store.count() == 0

    def test_delete_nonexistent(self, store):
        assert store.delete(9999) is False

    def test_clear(self, store):
        result = _make_result()
        store.add(result, "/tmp/a.wav")
        store.add(result, "/tmp/b.wav")
        n = store.clear()
        assert n == 2
        assert store.count() == 0


class TestHistorySearch:
    def test_search_finds_text(self, store):
        result = _make_result()
        store.add(result, "/tmp/audio.wav")
        records = store.search("Hello world")
        assert len(records) == 1

    def test_search_no_match(self, store):
        result = _make_result()
        store.add(result, "/tmp/audio.wav")
        records = store.search("xyzzy_not_found")
        assert len(records) == 0

    def test_search_empty_string_returns_all(self, store):
        result = _make_result()
        store.add(result, "/tmp/audio.wav")
        store.add(result, "/tmp/other.wav")
        records = store.search("")
        assert len(records) == 2

    def test_search_by_filename(self, store):
        result = _make_result()
        store.add(result, "/tmp/meeting_notes.wav")
        store.add(result, "/tmp/other.wav")
        records = store.search("meeting_notes")
        assert len(records) == 1
        assert records[0].source_name == "meeting_notes.wav"

    def test_search_returns_record_with_preview(self, store):
        result = _make_result()
        store.add(result, "/tmp/audio.wav")
        records = store.search("Hello")
        assert len(records) == 1
        # preview should be populated
        assert records[0].preview is not None

    def test_fts_or_like_available(self, store):
        # At minimum the store should report FTS availability
        assert isinstance(store.fts_available, bool)

    def test_search_cyrillic(self, store):
        from types import SimpleNamespace
        ru_segments = [SimpleNamespace(start=0.0, end=5.0, text="Привет мир", speaker=None)]
        result = SimpleNamespace(segments=ru_segments, language="ru", duration=5.0)
        store.add(result, "/tmp/ru_audio.wav")
        records = store.search("Привет")
        assert len(records) == 1

    def test_fts_query_removes_operators_from_user_input(self):
        query = _fts_query('hello* ^world "OR"')
        assert query == '"hello"* "world"* "OR"*'


class TestSpeakerNames:
    def test_speaker_names_round_trip(self, store):
        result = _make_result()
        names = {"Speaker 1": "Alice", "Speaker 2": "Bob"}
        rid = store.add(result, "/tmp/audio.wav", speaker_names=names)
        payload = store.get(rid)
        assert payload["speaker_names"] == names

    def test_speaker_names_default_empty(self, store):
        result = _make_result()
        rid = store.add(result, "/tmp/audio.wav")
        payload = store.get(rid)
        assert payload["speaker_names"] == {}


class TestSpeakerAliases:
    """B6, docs/IMPROVEMENT_PLAN_2026-08.ru.md: a reusable hint list for
    the speaker rename dialog, not cross-record identity — see
    core.history._v5_add_speaker_aliases_table's docstring."""

    def test_a_remembered_alias_is_listed(self, store):
        store.remember_speaker_alias("Alice")
        assert store.list_speaker_aliases() == ["Alice"]

    def test_list_is_empty_with_nothing_remembered(self, store):
        assert store.list_speaker_aliases() == []

    def test_upsert_is_idempotent_not_duplicating_rows(self, store):
        store.remember_speaker_alias("Alice")
        store.remember_speaker_alias("Alice")
        store.remember_speaker_alias("Alice")
        assert store.list_speaker_aliases() == ["Alice"]

    def test_most_used_alias_sorts_first(self, store):
        store.remember_speaker_alias("Bob")
        store.remember_speaker_alias("Alice")
        store.remember_speaker_alias("Alice")
        assert store.list_speaker_aliases() == ["Alice", "Bob"]

    def test_ties_broken_by_most_recently_used(self, store, monkeypatch):
        # remember_speaker_alias() timestamps at one-second resolution
        # (datetime.now(...).isoformat(timespec="seconds")) — two real
        # calls in the same test can land in the same second, making a
        # tie-break assertion flaky. Control the clock explicitly instead.
        import datetime as datetime_module
        import core.history as history_module

        ticks = iter([
            datetime_module.datetime(2026, 1, 1, 0, 0, 1, tzinfo=datetime_module.timezone.utc),
            datetime_module.datetime(2026, 1, 1, 0, 0, 2, tzinfo=datetime_module.timezone.utc),
            datetime_module.datetime(2026, 1, 1, 0, 0, 3, tzinfo=datetime_module.timezone.utc),
        ])

        class _FixedDatetime(datetime_module.datetime):
            @classmethod
            def now(cls, tz=None):
                return next(ticks)

        monkeypatch.setattr(history_module, "datetime", _FixedDatetime)

        store.remember_speaker_alias("Older")
        store.remember_speaker_alias("Newer")
        # Both used exactly once — the more recently touched one sorts first.
        assert store.list_speaker_aliases() == ["Newer", "Older"]
        store.remember_speaker_alias("Older")
        assert store.list_speaker_aliases() == ["Older", "Newer"]

    def test_limit_is_honoured(self, store):
        for name in ("A", "B", "C", "D"):
            store.remember_speaker_alias(name)
        assert len(store.list_speaker_aliases(limit=2)) == 2

    def test_blank_alias_is_not_remembered(self, store):
        store.remember_speaker_alias("")
        store.remember_speaker_alias("   ")
        assert store.list_speaker_aliases() == []

    def test_alias_is_stripped(self, store):
        store.remember_speaker_alias("  Alice  ")
        assert store.list_speaker_aliases() == ["Alice"]


class TestMultipleRecords:
    def test_newest_first(self, store):
        r1 = _make_result(language="en")
        r2 = _make_result(language="ru")
        store.add(r1, "/tmp/a.wav")
        store.add(r2, "/tmp/b.wav")
        records = store.list()
        assert len(records) == 2
        # newest (b.wav) should come first
        assert records[0].source_name == "b.wav"

    def test_limit_offset(self, store):
        r = _make_result()
        for i in range(5):
            store.add(r, f"/tmp/f{i}.wav")
        page1 = store.list(limit=2, offset=0)
        page2 = store.list(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        ids1 = {x.id for x in page1}
        ids2 = {x.id for x in page2}
        assert ids1.isdisjoint(ids2)


class TestSchemaMigrations:
    """core.history._migrate replaced two ad-hoc "attempt the ALTER TABLE,
    swallow duplicate-column errors" methods with a PRAGMA user_version-
    tracked sequence. These pin down both the fresh-database path and the
    upgrade path for a database written by that older code."""

    def _user_version(self, db_path) -> int:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()

    def test_fresh_database_ends_at_the_latest_version(self, tmp_path):
        db_path = tmp_path / "fresh.db"
        HistoryStore(db_path=db_path)
        assert self._user_version(db_path) == len(_MIGRATIONS)

    def test_fresh_database_has_every_migrated_column(self, tmp_path):
        db_path = tmp_path / "fresh.db"
        store = HistoryStore(db_path=db_path)
        rid = store.add(_make_result(), "/tmp/audio.wav", source_kind="live")
        record = store.get_record(rid)
        assert record["source_kind"] == "live"
        store.set_artifacts(rid, ["transcript", "youtube"])
        assert store.list()[0].artifacts == ["transcript", "youtube"]

    def test_reopening_an_already_migrated_database_is_a_no_op(self, tmp_path):
        """Simulates an app restart: the second open must not error and
        must not touch data written by the first."""
        db_path = tmp_path / "restart.db"
        store = HistoryStore(db_path=db_path)
        rid = store.add(_make_result(), "/tmp/audio.wav")

        store_again = HistoryStore(db_path=db_path)
        assert self._user_version(db_path) == len(_MIGRATIONS)
        assert store_again.get(rid) is not None

    def test_pre_user_version_database_migrates_without_error(self, tmp_path):
        """A database written by the pre-refactor code already has the
        artifacts/source_kind columns from a direct ALTER TABLE, but
        user_version was never touched (stays 0). _migrate must treat the
        resulting "duplicate column" as already-applied rather than
        crashing on startup, and must still record the version so later
        launches take the fast path."""
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript("""
                CREATE TABLE transcripts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at  TEXT    NOT NULL,
                    source_path TEXT    NOT NULL,
                    source_name TEXT    NOT NULL,
                    source_kind TEXT    NOT NULL DEFAULT 'file',
                    duration    REAL    NOT NULL DEFAULT 0,
                    language    TEXT    NOT NULL DEFAULT '',
                    model       TEXT    NOT NULL DEFAULT '',
                    json_payload TEXT   NOT NULL,
                    artifacts   TEXT
                );
            """)
            conn.execute(
                "INSERT INTO transcripts (created_at, source_path, source_name, "
                "duration, language, model, json_payload) "
                "VALUES ('2026-01-01T00:00:00', '/tmp/old.wav', 'old.wav', "
                "1.0, 'en', 'base', '{}')"
            )
            conn.commit()
        finally:
            conn.close()
        assert self._user_version(db_path) == 0

        store = HistoryStore(db_path=db_path)

        assert self._user_version(db_path) == len(_MIGRATIONS)
        records = store.list()
        assert len(records) == 1
        assert records[0].source_name == "old.wav"
        # The bridged database must still accept writes to the columns
        # that migration was supposed to add.
        rid = store.add(_make_result(), "/tmp/new.wav", source_kind="live")
        store.set_artifacts(rid, ["transcript"])
        assert store.get_record(rid)["source_kind"] == "live"

    def test_partially_migrated_database_only_runs_whats_new(self, tmp_path):
        """A database that already ran migration 1 (user_version=1) must
        not re-run it — only migrations 2..N are applied."""
        db_path = tmp_path / "partial.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript("""
                CREATE TABLE transcripts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at  TEXT    NOT NULL,
                    source_path TEXT    NOT NULL,
                    source_name TEXT    NOT NULL,
                    duration    REAL    NOT NULL DEFAULT 0,
                    language    TEXT    NOT NULL DEFAULT '',
                    model       TEXT    NOT NULL DEFAULT '',
                    json_payload TEXT   NOT NULL
                );
                CREATE INDEX idx_transcripts_created ON transcripts(created_at DESC);
            """)
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
        finally:
            conn.close()

        store = HistoryStore(db_path=db_path)

        assert self._user_version(db_path) == len(_MIGRATIONS)
        rid = store.add(_make_result(), "/tmp/audio.wav", source_kind="live")
        store.set_artifacts(rid, ["transcript"])
        assert store.get_record(rid)["source_kind"] == "live"


class TestFTSRebuildPolicy:
    """R13: FTS rebuild must only happen on first creation or explicit repair."""

    def test_second_init_does_not_rebuild(self, tmp_path):
        """After first creation the fts_state marker is 'ok'; a second
        HistoryStore opening the same file must not run rebuild."""
        import sqlite3 as _sqlite3
        from core.history import HistoryStore, _FTS_STATE_OK

        db = tmp_path / "history.db"

        # First init: creates FTS table, runs rebuild, writes fts_state='ok'
        store1 = HistoryStore(db_path=db)
        assert store1._fts_available

        # Verify state was written
        conn = _sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='fts_state'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None and row[0] == _FTS_STATE_OK, (
            f"fts_state not persisted after first init: {row}"
        )

        # Second init: fts_state is 'ok' → no rebuild
        # We verify by checking the FTS table still queries correctly
        store2 = HistoryStore(db_path=db)
        assert store2._fts_available

        # Sanity: searching on fresh db returns no results (not a crash)
        results = store2.search("hello")
        assert results == []

    def test_repair_fts_triggers_rebuild_on_next_init(self, tmp_path):
        """repair_fts() sets fts_state='repair_needed'; the next init then
        runs a full rebuild and resets state back to 'ok'."""
        import sqlite3 as _sqlite3
        from core.history import HistoryStore, _FTS_STATE_OK, _FTS_STATE_REPAIR

        db = tmp_path / "history.db"

        store1 = HistoryStore(db_path=db)
        assert store1._fts_available

        # Schedule repair
        store1.repair_fts()

        conn = _sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='fts_state'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None and row[0] == _FTS_STATE_REPAIR

        # Next init should detect repair_needed, run rebuild, reset to ok
        store2 = HistoryStore(db_path=db)
        assert store2._fts_available

        conn = _sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='fts_state'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None and row[0] == _FTS_STATE_OK


class TestArtifactTexts:
    """B7, docs/IMPROVEMENT_PLAN_2026-08.ru.md: search across generated
    materials (article/insights/youtube/book text) via artifact_texts."""

    def test_set_and_search_artifacts_finds_a_marker_word(self, store):
        rid = store.add(_make_result(), "/tmp/audio.wav", source_name="audio.wav")
        store.set_artifact_text(rid, "article", "/out/article.md", "an article about pricing")

        hits = store.search_artifacts("pricing")

        assert len(hits) == 1
        assert hits[0].record_id == rid
        assert hits[0].type == "article"
        assert hits[0].source_name == "audio.wav"
        assert "pricing" in hits[0].snippet.replace("**", "")

    def test_search_artifacts_with_empty_query_returns_nothing(self, store):
        rid = store.add(_make_result(), "/tmp/audio.wav")
        store.set_artifact_text(rid, "article", "/out/article.md", "some text")

        assert store.search_artifacts("") == []
        assert store.search_artifacts("   ") == []

    def test_set_artifact_text_upserts_by_record_and_type(self, store):
        rid = store.add(_make_result(), "/tmp/audio.wav")
        store.set_artifact_text(rid, "article", "/out/article.md", "about widgets")
        store.set_artifact_text(rid, "article", "/out/article.md", "about gadgets")

        assert store.search_artifacts("widgets") == []
        assert len(store.search_artifacts("gadgets")) == 1

    def test_set_artifact_text_truncates_to_the_indexed_cap(self, store):
        from core.history import _MAX_INDEXED_ARTIFACT_CHARS

        rid = store.add(_make_result(), "/tmp/audio.wav")
        long_text = "x" * (_MAX_INDEXED_ARTIFACT_CHARS + 5000) + " findableword"
        store.set_artifact_text(rid, "book", "/out/book.md", long_text)

        # The marker word sits past the cap, so it's simply not indexed —
        # not a crash, not a silently-oversized row.
        assert store.search_artifacts("findableword") == []
        hits = store.search_artifacts("x")
        assert hits and hits[0].type == "book"

    def test_different_records_and_types_do_not_collide(self, store):
        rid_a = store.add(_make_result(), "/tmp/a.wav", source_name="a.wav")
        rid_b = store.add(_make_result(), "/tmp/b.wav", source_name="b.wav")
        store.set_artifact_text(rid_a, "article", "/out/a-article.md", "about pricing")
        store.set_artifact_text(rid_a, "insights", "/out/a-insights.md", "chapter: onboarding")
        store.set_artifact_text(rid_b, "book", "/out/b-book.md", "a chapter about pricing too")

        hits = store.search_artifacts("pricing")

        assert {(h.record_id, h.type) for h in hits} == {(rid_a, "article"), (rid_b, "book")}

    def test_clear_wipes_artifact_texts_too(self, store):
        rid = store.add(_make_result(), "/tmp/audio.wav")
        store.set_artifact_text(rid, "article", "/out/article.md", "about pricing")

        store.clear()

        assert store.search_artifacts("pricing") == []

    def test_artifact_fts_available_reflects_index_state(self, store):
        assert isinstance(store.artifact_fts_available, bool)


class TestTranscriptRevisions:
    """B8, docs/IMPROVEMENT_PLAN_2026-08.ru.md: non-destructive transcript
    edit history."""

    @staticmethod
    def _payload(text: str) -> str:
        import json
        return json.dumps({"segments": [{"text": text}], "language": "en"})

    def test_first_version_is_saved(self, store):
        rid = store.add(_make_result(), "/tmp/a.wav")
        version_id = store.add_transcript_revision(rid, "rev1", self._payload("hello"))

        assert version_id is not None
        metas = store.list_transcript_revisions(rid)
        assert len(metas) == 1
        assert metas[0].id == version_id
        assert metas[0].word_count == 1

    def test_saving_the_same_revision_twice_is_skipped(self, store):
        rid = store.add(_make_result(), "/tmp/a.wav")
        store.add_transcript_revision(rid, "rev1", self._payload("hello"))
        second = store.add_transcript_revision(rid, "rev1", self._payload("hello"))

        assert second is None
        assert len(store.list_transcript_revisions(rid)) == 1

    def test_a_changed_revision_is_saved_as_a_new_version(self, store):
        rid = store.add(_make_result(), "/tmp/a.wav")
        store.add_transcript_revision(rid, "rev1", self._payload("hello"))
        store.add_transcript_revision(rid, "rev2", self._payload("hello world"))

        metas = store.list_transcript_revisions(rid)
        assert len(metas) == 2
        # newest first
        assert metas[0].revision == "rev2"
        assert metas[1].revision == "rev1"

    def test_size_delta_is_relative_to_the_previous_version(self, store):
        rid = store.add(_make_result(), "/tmp/a.wav")
        store.add_transcript_revision(rid, "rev1", self._payload("hi"))
        store.add_transcript_revision(rid, "rev2", self._payload("hi there"))

        metas = store.list_transcript_revisions(rid)
        newest, oldest = metas
        assert oldest.size_delta == 0
        assert newest.size_delta == newest.char_count - oldest.char_count
        assert newest.size_delta > 0

    def test_get_transcript_revision_returns_the_full_payload(self, store):
        rid = store.add(_make_result(), "/tmp/a.wav")
        version_id = store.add_transcript_revision(rid, "rev1", self._payload("hello"))

        payload = store.get_transcript_revision(version_id)
        assert payload["segments"][0]["text"] == "hello"

    def test_get_transcript_revision_of_an_unknown_id_is_none(self, store):
        assert store.get_transcript_revision(999) is None

    def test_pruning_keeps_the_first_version_and_the_newest_n_minus_one(self, store):
        rid = store.add(_make_result(), "/tmp/a.wav")
        first_id = store.add_transcript_revision(rid, "rev0", self._payload("v0"))
        for i in range(1, 25):
            store.add_transcript_revision(rid, f"rev{i}", self._payload(f"v{i}"), keep=5)

        metas = store.list_transcript_revisions(rid)
        ids = {m.id for m in metas}
        assert len(metas) == 5
        assert first_id in ids
        revisions = {m.revision for m in metas}
        assert revisions == {"rev0", "rev21", "rev22", "rev23", "rev24"}

    def test_pruning_with_keep_equal_to_one_keeps_only_the_first_version(self, store):
        rid = store.add(_make_result(), "/tmp/a.wav")
        first_id = store.add_transcript_revision(rid, "rev0", self._payload("v0"))
        store.add_transcript_revision(rid, "rev1", self._payload("v1"), keep=1)
        store.add_transcript_revision(rid, "rev2", self._payload("v2"), keep=1)

        metas = store.list_transcript_revisions(rid)
        assert [m.id for m in metas] == [first_id]

    def test_revisions_are_scoped_per_record(self, store):
        rid_a = store.add(_make_result(), "/tmp/a.wav")
        rid_b = store.add(_make_result(), "/tmp/b.wav")
        store.add_transcript_revision(rid_a, "rev1", self._payload("a text"))
        store.add_transcript_revision(rid_b, "rev1", self._payload("b text"))

        assert len(store.list_transcript_revisions(rid_a)) == 1
        assert len(store.list_transcript_revisions(rid_b)) == 1

    def test_delete_removes_transcript_revisions_for_that_record(self, store):
        rid = store.add(_make_result(), "/tmp/a.wav")
        store.add_transcript_revision(rid, "rev1", self._payload("hello"))

        store.delete(rid)

        assert store.list_transcript_revisions(rid) == []

    def test_clear_wipes_transcript_revisions_too(self, store):
        rid = store.add(_make_result(), "/tmp/a.wav")
        store.add_transcript_revision(rid, "rev1", self._payload("hello"))

        store.clear()

        assert store.list_transcript_revisions(rid) == []
