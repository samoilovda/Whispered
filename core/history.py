"""
Whispered – Transcription History Store
SQLite-backed persistence for past transcription results.
FTS5 full-text search with unicode61 tokeniser (Cyrillic-aware).
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generator, List, Optional

from core.logger import get_logger
from core.paths import history_path

logger = get_logger(__name__)

_DB_PATH = history_path()


def _v1_initial_schema(conn: sqlite3.Connection) -> None:
    """The table as first shipped. Kept exactly as-is — including the
    absence of ``artifacts``/``source_kind`` — so ``user_version`` reflects
    a database's real migration history rather than its current shape."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS transcripts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT    NOT NULL,
            source_path TEXT    NOT NULL,
            source_name TEXT    NOT NULL,
            duration    REAL    NOT NULL DEFAULT 0,
            language    TEXT    NOT NULL DEFAULT '',
            model       TEXT    NOT NULL DEFAULT '',
            json_payload TEXT   NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_transcripts_created ON transcripts(created_at DESC);
    """)


def _v2_add_artifacts_column(conn: sqlite3.Connection) -> None:
    """Records which generated artifacts (transcript/youtube/article) exist
    for a row, as a JSON array of type strings. NULL for rows written
    before this migration or for records where no preset chain (Phase C.3)
    has run yet; the Library falls back to showing just the guaranteed
    "transcript" chip in that case."""
    conn.execute("ALTER TABLE transcripts ADD COLUMN artifacts TEXT")


def _v3_add_source_kind_column(conn: sqlite3.Connection) -> None:
    """Explicit source kind (e.g. "file" vs "live"), so callers can filter
    without guessing from the filename."""
    conn.execute(
        "ALTER TABLE transcripts ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'file'"
    )


def _v4_add_job_runs_table(conn: sqlite3.Connection) -> None:
    """Persisted JobRun state (see docs/UI_REDESIGN_PLAN_2026-09.ru.md,
    B3, and application/run_store.py) — which steps of a recipe run
    succeeded/failed for a given history record, so "retry one step"
    survives an app restart and the Library can show a run's composition
    without the JobRun object still being held in memory. Read/written
    exclusively through application/run_store.py; this migration only
    creates the table."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS job_runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id     INTEGER NOT NULL,
            recipe        TEXT    NOT NULL,
            started_at    TEXT    NOT NULL,
            finished_at   TEXT,
            status        TEXT    NOT NULL DEFAULT 'running',
            outcomes_json TEXT    NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_job_runs_record ON job_runs(record_id);
    """)


def _v5_add_speaker_aliases_table(conn: sqlite3.Connection) -> None:
    """Speaker names a user has typed while renaming a diarized speaker in
    any record (B6, docs/IMPROVEMENT_PLAN_2026-08.ru.md) — a reusable
    hint list, not cross-record identity: diarization gives no speaker
    embedding, so "SPEAKER_00" in two different files are unrelated
    objects and this table never claims otherwise. Read/written
    exclusively through HistoryStore.remember_speaker_alias()/
    list_speaker_aliases()."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS speaker_aliases (
            alias       TEXT PRIMARY KEY,
            used_count  INTEGER NOT NULL DEFAULT 1,
            updated_at  TEXT    NOT NULL
        );
    """)


def _v6_add_artifact_texts(conn: sqlite3.Connection) -> None:
    """Flat, searchable text extracted from generated materials — article
    bodies, insight titles/bullets, a YouTube package's fields, a book's
    chapters (B7, docs/IMPROVEMENT_PLAN_2026-08.ru.md item 1). FTS5 only
    ever indexed transcripts(source_name, json_payload); this is what
    lets "where did I talk about pricing" also find "where was the
    article about pricing". Written exclusively through
    application/steps.py's per-type extractors (via
    HistoryStore.set_artifact_text()), one row per (record, type) —
    UNIQUE enforces that a step's re-run replaces its own row rather
    than accumulating stale copies."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS artifact_texts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id  INTEGER NOT NULL,
            type       TEXT    NOT NULL,
            path       TEXT    NOT NULL,
            text       TEXT    NOT NULL,
            updated_at TEXT    NOT NULL,
            UNIQUE(record_id, type)
        );
        CREATE INDEX IF NOT EXISTS idx_artifact_texts_record ON artifact_texts(record_id);
    """)


def _v7_add_transcript_revisions(conn: sqlite3.Connection) -> None:
    """Non-destructive transcript edit history (B8,
    docs/IMPROVEMENT_PLAN_2026-08.ru.md) — a full ``json_payload`` per
    saved version, the same shape ``transcripts.json_payload`` already
    uses, so a version can be handed straight to ``DocumentSession.
    apply_result()`` on restore without a separate deserialization path.
    Written exclusively through ``HistoryStore.add_transcript_revision()``,
    which also prunes old rows down to ``Config.transcript_revisions_kept``
    — a full transcript per version) is the "start simple" option B8's own
    risk note accepts, not an oversight; diffing instead of storing full
    copies is the fallback if this turns out to bloat the database.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS transcript_revisions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id    INTEGER NOT NULL,
            revision     TEXT    NOT NULL,
            created_at   TEXT    NOT NULL,
            json_payload TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_revisions_record ON transcript_revisions(record_id, id DESC);
    """)


# Applied in order, tracked via SQLite's built-in `PRAGMA user_version`
# (see HistoryStore._migrate). Append new migrations here — never edit or
# reorder an existing one, since a database's user_version records exactly
# how many of these it has already run.
_MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _v1_initial_schema,
    _v2_add_artifacts_column,
    _v3_add_source_kind_column,
    _v4_add_job_runs_table,
    _v5_add_speaker_aliases_table,
    _v6_add_artifact_texts,
    _v7_add_transcript_revisions,
)

# FTS5 schema — created separately so failures (no FTS5 compile) are handled gracefully.
_CREATE_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
    source_name,
    json_payload,
    content=transcripts,
    content_rowid=id,
    tokenize="unicode61"
);

CREATE TRIGGER IF NOT EXISTS transcripts_fts_ai
AFTER INSERT ON transcripts BEGIN
    INSERT INTO transcripts_fts(rowid, source_name, json_payload)
    VALUES (new.id, new.source_name, new.json_payload);
END;

CREATE TRIGGER IF NOT EXISTS transcripts_fts_ad
AFTER DELETE ON transcripts BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, source_name, json_payload)
    VALUES ('delete', old.id, old.source_name, old.json_payload);
END;

CREATE TRIGGER IF NOT EXISTS transcripts_fts_au
AFTER UPDATE ON transcripts BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, source_name, json_payload)
    VALUES ('delete', old.id, old.source_name, old.json_payload);
    INSERT INTO transcripts_fts(rowid, source_name, json_payload)
    VALUES (new.id, new.source_name, new.json_payload);
END;
"""

# Second FTS5 mirror, over artifact_texts (B7) — generated materials
# (article/insights/youtube/book), not the transcript itself. Same
# content-table/triggers shape as _CREATE_FTS_SQL above, just over a
# different source table and column set.
_CREATE_ARTIFACT_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS artifact_texts_fts USING fts5(
    text,
    content=artifact_texts,
    content_rowid=id,
    tokenize="unicode61"
);

CREATE TRIGGER IF NOT EXISTS artifact_texts_fts_ai
AFTER INSERT ON artifact_texts BEGIN
    INSERT INTO artifact_texts_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS artifact_texts_fts_ad
AFTER DELETE ON artifact_texts BEGIN
    INSERT INTO artifact_texts_fts(artifact_texts_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS artifact_texts_fts_au
AFTER UPDATE ON artifact_texts BEGIN
    INSERT INTO artifact_texts_fts(artifact_texts_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
    INSERT INTO artifact_texts_fts(rowid, text) VALUES (new.id, new.text);
END;
"""

# Persistent key-value metadata table used to track FTS state.
# Stored inside the same SQLite file to avoid a separate sidecar.
_CREATE_SCHEMA_META_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Values stored under an FTS state key ("fts_state" for transcripts_fts,
# "fts_state_artifacts" for artifact_texts_fts).
_FTS_STATE_OK = "ok"
_FTS_STATE_REPAIR = "repair_needed"

# Cap on how much text of one material gets indexed (B7's own risk note):
# a book's assembled text can run to megabytes, an order of magnitude
# more than search needs to be useful. Article/insights/YouTube fields
# are nowhere close to this in practice.
_MAX_INDEXED_ARTIFACT_CHARS = 200_000


def _result_to_payload(result: Any, model: str = "", speaker_names: dict | None = None) -> str:
    """Serialize a TranscriptionResult to a JSON string for storage."""
    segments = [
        {
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
            "speaker": seg.speaker,
            "words": [
                {"start": word.start, "end": word.end, "text": word.text}
                for word in getattr(seg, "words", [])
            ],
        }
        for seg in result.segments
    ]
    # Prefer explicit names; fall back to whatever the result carries.
    names = speaker_names or getattr(result, "speaker_names", None) or {}
    data = {
        "language": result.language,
        "duration": result.duration,
        "model": model,
        "speaker_names": names,
        "segments": segments,
    }
    return json.dumps(data, ensure_ascii=False)


def _payload_to_dict(payload: str) -> dict:
    """Deserialize stored JSON payload."""
    try:
        return json.loads(payload)
    except Exception as exc:
        logger.warning("Failed to deserialize history payload: %s", exc)
        return {}


def _fts_query(text: str) -> str:
    """Build a safe FTS5 query from user input.

    Appends '*' for prefix matching; escapes double-quotes.
    """
    # Quote every token as a literal prefix term. Strip FTS5 syntax characters
    # from user input before adding our own quotes and trailing wildcard.
    clean = text.replace('"', '').replace('*', '').replace('^', '').strip()
    if not clean:
        return '""'
    # Each whitespace-separated token is prefix-matched
    tokens = clean.split()
    return " ".join(f'"{t}"*' for t in tokens)


class HistoryRecord:
    """Lightweight metadata record returned by list()/search().

    Uses sqlite3.Row (dict-like) so field access is by name, not position.
    This is robust to future changes in SQL column order.
    """

    __slots__ = ("id", "created_at", "source_path", "source_name", "source_kind",
                 "duration", "language", "model", "preview", "artifacts")

    def __init__(self, row: sqlite3.Row):
        self.id          = row["id"]
        self.created_at  = row["created_at"]
        self.source_path = row["source_path"]
        self.source_name = row["source_name"]
        self.source_kind = row["source_kind"] if "source_kind" in row.keys() else "file"
        self.duration    = row["duration"]
        self.language    = row["language"]
        self.model       = row["model"]
        self.preview     = row["preview"]
        raw_artifacts = row["artifacts"]
        try:
            self.artifacts: list[str] | None = json.loads(raw_artifacts) if raw_artifacts else None
        except (TypeError, json.JSONDecodeError):
            logger.warning("Ignoring malformed history artifacts for record %s", self.id)
            self.artifacts = None


class HistoryStore:
    """
    Thin SQLite wrapper for transcription history.
    Thread-safe via check_same_thread=False (caller must not share connections).
    FTS5 is used when available (compiled in); falls back to LIKE search.
    """

    def __init__(self, db_path: Path = _DB_PATH):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._fts_available: bool = False
        self._artifact_fts_available: bool = False
        self._init_db()
        if os.name != "nt":
            try:
                self._db_path.chmod(0o600)
            except OSError:
                logger.warning(
                    "Could not restrict history database permissions: %s",
                    self._db_path,
                )

    # ------------------------------------------------------------------ init

    def _init_db(self):
        with self._connect() as conn:
            # journal_mode is persistent DB state, so set it once during
            # initialization rather than issuing a write-like PRAGMA for every
            # short-lived connection.
            conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()
        self._init_fts()

    def _migrate(self) -> None:
        """Bring the database schema up to date.

        Tracked via SQLite's own ``PRAGMA user_version`` rather than the
        former "attempt every ALTER TABLE, swallow duplicate-column errors"
        approach: that made a database's schema history implicit and meant
        each new column needed its own hand-written idempotency check. A
        fresh database starts at user_version 0 and runs every migration in
        ``_MIGRATIONS``; an existing one only runs what's new, then records
        how far it got so the next launch skips straight to the check.

        A database written by that older code may already carry the
        ``artifacts``/``source_kind`` columns from a direct ALTER TABLE, but
        with ``user_version`` still at 0 — it never touched the pragma. The
        duplicate-column/table catch below is exactly for that one-time
        bridge; once it has run, this database's user_version matches
        ``len(_MIGRATIONS)`` and every future launch takes the fast path.
        """
        with self._connect() as conn:
            current_version = conn.execute("PRAGMA user_version").fetchone()[0]
            for version, migrate in enumerate(_MIGRATIONS, start=1):
                if version <= current_version:
                    continue
                try:
                    migrate(conn)
                except sqlite3.OperationalError as exc:
                    message = str(exc).lower()
                    if "duplicate column" not in message and "already exists" not in message:
                        raise
                    logger.debug(
                        "History migration %d already applied on disk: %s", version, exc
                    )
                # PRAGMA does not accept bound parameters; `version` is our
                # own loop counter, never external input.
                conn.execute(f"PRAGMA user_version = {version}")

    def _init_fts(self) -> None:
        """Create both FTS5 indexes if needed; set _fts_available /
        _artifact_fts_available accordingly (B7 — two indexes,
        transcripts_fts and artifact_texts_fts, sharing the same
        create-and-rebuild-if-needed logic via _init_one_fts())."""
        try:
            with self._connect() as conn:
                # Ensure the metadata tracking table exists.
                conn.executescript(_CREATE_SCHEMA_META_SQL)
                self._init_one_fts(
                    conn, table="transcripts_fts",
                    create_sql=_CREATE_FTS_SQL, state_key="fts_state",
                )
            self._fts_available = True
        except sqlite3.OperationalError as exc:
            logger.warning("FTS5 not available — falling back to LIKE search: %s", exc)
            self._fts_available = False

        try:
            with self._connect() as conn:
                conn.executescript(_CREATE_SCHEMA_META_SQL)
                self._init_one_fts(
                    conn, table="artifact_texts_fts",
                    create_sql=_CREATE_ARTIFACT_FTS_SQL, state_key="fts_state_artifacts",
                )
            self._artifact_fts_available = True
        except sqlite3.OperationalError as exc:
            logger.warning(
                "Artifact-text FTS5 not available — falling back to LIKE search: %s", exc
            )
            self._artifact_fts_available = False

    @staticmethod
    def _init_one_fts(
        conn: sqlite3.Connection, *, table: str, create_sql: str, state_key: str,
    ) -> None:
        """Create *table* if needed and rebuild it exactly once — the
        first time it's created, or when *state_key* in schema_meta reads
        ``repair_needed`` (set by :meth:`repair_fts`). Every subsequent
        launch skips the rebuild and takes the fast path. Shared by
        _init_fts() for both transcripts_fts and artifact_texts_fts (B7)
        rather than duplicated per index."""
        fts_state_row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (state_key,)
        ).fetchone()
        current_state = fts_state_row[0] if fts_state_row else None

        fts_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table,)
        ).fetchone() is not None

        conn.executescript(create_sql)

        need_rebuild = (
            not fts_exists  # first time — table just created
            or current_state == _FTS_STATE_REPAIR  # explicit repair request
        )
        if need_rebuild:
            logger.debug("FTS5 (%s): running full index rebuild (state=%s)", table, current_state)
            conn.execute(f"INSERT INTO {table}({table}) VALUES ('rebuild')")
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                (state_key, _FTS_STATE_OK),
            )
        else:
            logger.debug("FTS5 (%s): index already up-to-date, skipping rebuild", table)

    def repair_fts(self) -> None:
        """Schedule a full rebuild of both FTS5 indexes on the next
        :class:`HistoryStore` init.

        Call this when an index is suspected to be corrupt (e.g. after an
        unclean shutdown interrupted a rebuild). The repair itself is
        deferred to the next time :meth:`_init_fts` runs so this method is
        safe to call from any thread without holding a connection.
        """
        try:
            with self._connect() as conn:
                for state_key in ("fts_state", "fts_state_artifacts"):
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                        (state_key, _FTS_STATE_REPAIR),
                    )
            logger.info("FTS5: repair scheduled — will rebuild on next HistoryStore init")
        except sqlite3.OperationalError as exc:
            logger.warning("Could not schedule FTS repair: %s", exc)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row   # named-column access
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ public API

    def add(self, result: Any, source_path: str, model: str = "",
            speaker_names: dict | None = None, source_kind: str = "file",
            source_name: str | None = None) -> int:
        """Persist a TranscriptionResult; returns the new row id."""
        payload = _result_to_payload(result, model, speaker_names)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        source_name = source_name or Path(source_path).name or "Live transcript"
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO transcripts
                   (created_at, source_path, source_name, source_kind, duration, language, model, json_payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, source_path, source_name, source_kind, result.duration,
                 result.language, model, payload),
            )
            # lastrowid is Optional only before a successful INSERT; the
            # execute() above either inserted a row or raised.
            assert cur.lastrowid is not None
            return cur.lastrowid

    # NB: this method shadows the builtin inside the class body, so every
    # annotation below must spell the type as List[...] from typing.
    def list(self, limit: int = 200, offset: int = 0) -> List[HistoryRecord]:
        """Return lightweight metadata rows, newest first."""
        sql = """
            SELECT id, created_at, source_path, source_name, source_kind, duration, language, model,
                   artifacts, substr(json_payload, 1, 300) AS preview
            FROM transcripts
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (limit, offset)).fetchall()
        return [HistoryRecord(r) for r in rows]

    def get(self, record_id: int) -> Optional[dict]:
        """Return the full JSON payload dict for a given id, or None."""
        record = self.get_record(record_id)
        return record["payload"] if record is not None else None

    def get_record(self, record_id: int) -> Optional[dict[str, Any]]:
        """Return a complete history record, including its media metadata.

        Loading a transcript without its stored ``source_path`` is unsafe: a
        caller can otherwise retain the player and export context of the
        previously opened recording.  Keep the payload and the metadata in a
        single read so callers can switch that context atomically.
        """
        with self._connect() as conn:
            row = conn.execute(
                """SELECT id, source_path, source_name, source_kind, duration, language, model,
                          artifacts, json_payload
                   FROM transcripts WHERE id = ?""",
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            artifacts = json.loads(row["artifacts"]) if row["artifacts"] else ["transcript"]
        except (TypeError, json.JSONDecodeError):
            artifacts = ["transcript"]
        return {
            "id": row["id"],
            "source_path": row["source_path"],
            "source_name": row["source_name"],
            "source_kind": row["source_kind"],
            "duration": row["duration"],
            "language": row["language"],
            "model": row["model"],
            "artifacts": artifacts,
            "payload": _payload_to_dict(row["json_payload"]),
        }

    def get_source_name(self, record_id: int) -> Optional[str]:
        """Return the stored source_name (original filename) for a record, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT source_name FROM transcripts WHERE id = ?", (record_id,)
            ).fetchone()
        return row["source_name"] if row else None

    def set_artifacts(self, record_id: int, artifact_types: List[str]) -> None:
        """Record which artifact types (e.g. ["transcript", "youtube",
        "article"]) exist for a record — written once a preset chain
        (Phase C.3) finishes generating them. The Library's chip line
        reads this back via HistoryRecord.artifacts."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE transcripts SET artifacts = ? WHERE id = ?",
                (json.dumps(artifact_types, ensure_ascii=False), record_id),
            )

    def remember_speaker_alias(self, alias: str) -> None:
        """Record a name typed while renaming a diarized speaker, for
        reuse as a hint in a later record (B6, docs/IMPROVEMENT_PLAN_2026-08.ru.md).

        Upserts by *alias* — a name used again bumps its own count and
        timestamp rather than creating a duplicate row, so
        list_speaker_aliases()'s "most used, most recent" order reflects
        real usage. A blank alias is silently ignored: nothing useful to
        remember, and it would otherwise become the top suggestion for
        every future rename.
        """
        alias = alias.strip()
        if not alias:
            return
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO speaker_aliases (alias, used_count, updated_at)
                   VALUES (?, 1, ?)
                   ON CONFLICT(alias) DO UPDATE SET
                       used_count = used_count + 1,
                       updated_at = excluded.updated_at""",
                (alias, now),
            )

    def list_speaker_aliases(self, limit: int = 20) -> List[str]:
        """Previously used speaker names, most-used first (ties broken by
        most recent) — what a rename dialog elsewhere pre-fills as
        suggestions. Not cross-record speaker identity (diarization gives
        no such thing — see the B6 module note above
        _v5_add_speaker_aliases_table): a hint list only, never applied
        automatically."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT alias FROM speaker_aliases "
                "ORDER BY used_count DESC, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [row["alias"] for row in rows]

    def update_result(self, record_id: int, result: Any,
                      speaker_names: dict | None = None) -> bool:
        """Replace a stored result without creating a duplicate history row."""
        payload = _result_to_payload(result, speaker_names=speaker_names)
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE transcripts
                   SET duration = ?, language = ?, json_payload = ?, artifacts = ?
                   WHERE id = ?""",
                (
                    result.duration,
                    result.language,
                    payload,
                    json.dumps(["transcript"], ensure_ascii=False),
                    record_id,
                ),
            )
            return cur.rowcount > 0

    def delete(self, record_id: int) -> bool:
        """Delete a single record (and everything keyed off it — material
        texts, transcript versions) so those don't linger as orphans once
        the record itself is gone. Returns True if the transcript row was
        removed."""
        with self._connect() as conn:
            conn.execute("DELETE FROM artifact_texts WHERE record_id = ?", (record_id,))
            conn.execute("DELETE FROM transcript_revisions WHERE record_id = ?", (record_id,))
            cur = conn.execute("DELETE FROM transcripts WHERE id = ?", (record_id,))
            return cur.rowcount > 0

    def clear(self) -> int:
        """Delete all records. Returns the number of rows removed."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM transcripts")
            if self._fts_available:
                try:
                    # Rebuild the empty index to free disk space
                    conn.execute(
                        "INSERT INTO transcripts_fts(transcripts_fts) VALUES ('rebuild')"
                    )
                except Exception as exc:
                    logger.debug("FTS5 rebuild after clear failed (non-critical): %s", exc)
            conn.execute("DELETE FROM artifact_texts")
            if self._artifact_fts_available:
                try:
                    conn.execute(
                        "INSERT INTO artifact_texts_fts(artifact_texts_fts) VALUES ('rebuild')"
                    )
                except Exception as exc:
                    logger.debug(
                        "Artifact FTS5 rebuild after clear failed (non-critical): %s", exc
                    )
            conn.execute("DELETE FROM transcript_revisions")
            return cur.rowcount

    def search(self, text: str, limit: int = 100) -> List[HistoryRecord]:
        """Search across transcripts; uses FTS5 when available, else LIKE."""
        if not text.strip():
            return self.list(limit=limit)
        if self._fts_available:
            return self._search_fts(text, limit)
        return self._search_like(text, limit)

    def _search_fts(self, text: str, limit: int) -> List[HistoryRecord]:
        query = _fts_query(text)
        # snippet() highlights column 1 (json_payload); column 0 is source_name
        sql = """
            SELECT t.id, t.created_at, t.source_path, t.source_name, t.source_kind,
                   t.duration, t.language, t.model, t.artifacts,
                   snippet(transcripts_fts, 1, '**', '**', '…', 20) AS preview
            FROM transcripts_fts
            JOIN transcripts t ON t.id = transcripts_fts.rowid
            WHERE transcripts_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(sql, (query, limit)).fetchall()
            return [HistoryRecord(r) for r in rows]
        except sqlite3.OperationalError as exc:
            logger.warning("FTS5 search failed, falling back to LIKE: %s", exc)
            return self._search_like(text, limit)

    def _search_like(self, text: str, limit: int) -> List[HistoryRecord]:
        pattern = f"%{text}%"
        sql = """
            SELECT id, created_at, source_path, source_name, source_kind, duration, language, model,
                   artifacts, substr(json_payload, 1, 300) AS preview
            FROM transcripts
            WHERE json_payload LIKE ? OR source_name LIKE ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (pattern, pattern, limit)).fetchall()
        return [HistoryRecord(r) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]

    @property
    def fts_available(self) -> bool:
        return self._fts_available

    @property
    def artifact_fts_available(self) -> bool:
        return self._artifact_fts_available

    # ------------------------------------------------------------------ artifact text (B7)

    def set_artifact_text(self, record_id: int, artifact_type: str, path: str, text: str) -> None:
        """Upsert the flat, searchable text for one generated material
        (article/insights/youtube/book) belonging to *record_id*.

        Called from application/steps.py's per-type extractors after a
        step succeeds. Truncates to _MAX_INDEXED_ARTIFACT_CHARS (a book's
        assembled text can run to megabytes) — search still works on the
        truncated text, it just won't match a hit past the cap.
        """
        text = text[:_MAX_INDEXED_ARTIFACT_CHARS]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO artifact_texts (record_id, type, path, text, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(record_id, type) DO UPDATE SET
                       path = excluded.path,
                       text = excluded.text,
                       updated_at = excluded.updated_at""",
                (record_id, artifact_type, path, text, now),
            )

    def search_artifacts(self, text: str, limit: int = 100) -> List["ArtifactSearchResult"]:
        """Search generated materials (article/insights/youtube/book text
        indexed via set_artifact_text()); uses FTS5 when available, else
        LIKE. An empty query returns no results — unlike search(), there
        is no "browse all materials" view backing this."""
        if not text.strip():
            return []
        if self._artifact_fts_available:
            return self._search_artifacts_fts(text, limit)
        return self._search_artifacts_like(text, limit)

    def _search_artifacts_fts(self, text: str, limit: int) -> List["ArtifactSearchResult"]:
        query = _fts_query(text)
        sql = """
            SELECT a.record_id, a.type, a.path, t.source_name, t.source_kind,
                   snippet(artifact_texts_fts, 0, '**', '**', '…', 20) AS snippet
            FROM artifact_texts_fts
            JOIN artifact_texts a ON a.id = artifact_texts_fts.rowid
            JOIN transcripts t ON t.id = a.record_id
            WHERE artifact_texts_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(sql, (query, limit)).fetchall()
            return [ArtifactSearchResult(r) for r in rows]
        except sqlite3.OperationalError as exc:
            logger.warning("Artifact FTS5 search failed, falling back to LIKE: %s", exc)
            return self._search_artifacts_like(text, limit)

    def _search_artifacts_like(self, text: str, limit: int) -> List["ArtifactSearchResult"]:
        pattern = f"%{text}%"
        sql = """
            SELECT a.record_id, a.type, a.path, t.source_name, t.source_kind,
                   substr(a.text, 1, 300) AS snippet
            FROM artifact_texts a
            JOIN transcripts t ON t.id = a.record_id
            WHERE a.text LIKE ?
            ORDER BY a.updated_at DESC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (pattern, limit)).fetchall()
        return [ArtifactSearchResult(r) for r in rows]

    # ------------------------------------------------------------------ transcript versions (B8)

    def add_transcript_revision(
        self, record_id: int, revision: str, json_payload: str, keep: int = 20,
    ) -> Optional[int]:
        """Save a new transcript version, unless *revision* matches the
        most recently saved one for this record (nothing actually
        changed — a debounced caller re-firing on an edit that ended up a
        no-op, or a version write racing a duplicate save). Returns the
        new row id, or ``None`` when the write was skipped.

        Prunes down to *keep* rows afterwards, oldest first, but never
        deletes the very first version ("as transcribed") — that one is
        the baseline every later diff/restore ultimately traces back to.
        """
        with self._connect() as conn:
            last = conn.execute(
                "SELECT revision FROM transcript_revisions "
                "WHERE record_id = ? ORDER BY id DESC LIMIT 1",
                (record_id,),
            ).fetchone()
            if last is not None and last["revision"] == revision:
                return None

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cur = conn.execute(
                "INSERT INTO transcript_revisions (record_id, revision, created_at, json_payload) "
                "VALUES (?, ?, ?, ?)",
                (record_id, revision, now, json_payload),
            )
            new_id = cur.lastrowid
            assert new_id is not None

            ids = [
                row["id"] for row in conn.execute(
                    "SELECT id FROM transcript_revisions WHERE record_id = ? ORDER BY id ASC",
                    (record_id,),
                ).fetchall()
            ]
            if len(ids) > keep:
                # Keep the very first version plus the newest (keep - 1);
                # drop whatever's left in between.
                newest = ids[-(keep - 1):] if keep > 1 else []
                keep_ids = {ids[0], *newest}
                to_delete = [i for i in ids if i not in keep_ids]
                if to_delete:
                    conn.executemany(
                        "DELETE FROM transcript_revisions WHERE id = ?",
                        [(i,) for i in to_delete],
                    )
            return new_id

    def list_transcript_revisions(self, record_id: int) -> List["TranscriptRevisionMeta"]:
        """Lightweight metadata for every kept version of *record_id*,
        newest first — what the "Версии" dialog lists. Word count is
        computed from each version's own payload; a version's size delta
        is measured against the version immediately before it
        chronologically (the previous row in ``list()`` order, i.e. the
        next-oldest one), not against the very first version."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, revision, created_at, json_payload FROM transcript_revisions "
                "WHERE record_id = ? ORDER BY id DESC",
                (record_id,),
            ).fetchall()
        metas = [TranscriptRevisionMeta(r) for r in rows]
        for i, meta in enumerate(metas):
            older = metas[i + 1] if i + 1 < len(metas) else None
            meta.size_delta = meta.char_count - older.char_count if older is not None else 0
        return metas

    def get_transcript_revision(self, revision_id: int) -> Optional[dict[str, Any]]:
        """Full payload dict for one saved version, or ``None`` — the
        shape ``DocumentSession.apply_result()`` needs to restore it (via
        the same ``_payload_to_dict``/reconstruction path as a normal
        history record)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT json_payload FROM transcript_revisions WHERE id = ?",
                (revision_id,),
            ).fetchone()
        if row is None:
            return None
        return _payload_to_dict(row["json_payload"])

    def save_current_revision(
        self, record_id: int, result: Any, speaker_names: dict | None = None, keep: int = 20,
    ) -> Optional[int]:
        """Compute *result*'s ``transcript_revision`` hash and save it as
        a new version if it differs from the last saved one — used by the
        debounced auto-save on manual edits (B8) and by the initial "as
        transcribed" version written right after a fresh transcription
        (or a live capture) is added to history. Keeps the revision-hash/
        payload-serialization logic in one place instead of MainWindow
        duplicating ``_result_to_payload`` and
        ``application.artifact_provenance.transcript_revision`` itself.
        """
        from application.artifact_provenance import transcript_revision as _revision

        revision = _revision(result.segments, result.language)
        payload = _result_to_payload(result, speaker_names=speaker_names)
        return self.add_transcript_revision(record_id, revision, payload, keep=keep)


class TranscriptRevisionMeta:
    """One row from HistoryStore.list_transcript_revisions() — what the
    "Версии" dialog shows per version: id, timestamp, word count, and how
    much the text grew/shrank versus the version right before it.
    ``size_delta`` starts at 0 and is filled in by the caller (needs the
    whole list to know each version's neighbour), not computed here."""

    __slots__ = ("id", "revision", "created_at", "word_count", "char_count", "size_delta")

    def __init__(self, row: sqlite3.Row):
        self.id         = row["id"]
        self.revision   = row["revision"]
        self.created_at = row["created_at"]
        payload = _payload_to_dict(row["json_payload"])
        text = " ".join(
            str(seg.get("text", "")) for seg in payload.get("segments", [])
            if isinstance(seg, dict)
        )
        self.word_count = len(text.split())
        self.char_count = len(text)
        self.size_delta = 0


class ArtifactSearchResult:
    """One hit from HistoryStore.search_artifacts()."""

    __slots__ = ("record_id", "type", "path", "source_name", "source_kind", "snippet")

    def __init__(self, row: sqlite3.Row):
        self.record_id   = row["record_id"]
        self.type        = row["type"]
        self.path        = row["path"]
        self.source_name = row["source_name"]
        self.source_kind = row["source_kind"] if "source_kind" in row.keys() else "file"
        self.snippet     = row["snippet"]


# Module-level singleton (lazy)
_store: Optional[HistoryStore] = None


def get_history_store() -> HistoryStore:
    global _store
    if _store is None:
        _store = HistoryStore()
    return _store
