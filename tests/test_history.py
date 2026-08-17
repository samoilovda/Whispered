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
