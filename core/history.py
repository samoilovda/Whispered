"""
Whispered – Transcription History Store
SQLite-backed persistence for past transcription results.
FTS5 full-text search with unicode61 tokeniser (Cyrillic-aware).
"""

from __future__ import annotations

import json
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


# Applied in order, tracked via SQLite's built-in `PRAGMA user_version`
# (see HistoryStore._migrate). Append new migrations here — never edit or
# reorder an existing one, since a database's user_version records exactly
# how many of these it has already run.
_MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _v1_initial_schema,
    _v2_add_artifacts_column,
    _v3_add_source_kind_column,
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
        self._init_db()

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

    def _init_fts(self):
        """Attempt to create the FTS5 index; set _fts_available accordingly."""
        try:
            with self._connect() as conn:
                conn.executescript(_CREATE_FTS_SQL)
                # Rebuild index to cover any rows inserted before FTS was created.
                conn.execute("INSERT INTO transcripts_fts(transcripts_fts) VALUES ('rebuild')")
            self._fts_available = True
        except sqlite3.OperationalError as exc:
            logger.warning("FTS5 not available — falling back to LIKE search: %s", exc)
            self._fts_available = False

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
                          json_payload
                   FROM transcripts WHERE id = ?""",
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "source_path": row["source_path"],
            "source_name": row["source_name"],
            "source_kind": row["source_kind"],
            "duration": row["duration"],
            "language": row["language"],
            "model": row["model"],
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
        """Delete a single record. Returns True if a row was removed."""
        with self._connect() as conn:
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


# Module-level singleton (lazy)
_store: Optional[HistoryStore] = None


def get_history_store() -> HistoryStore:
    global _store
    if _store is None:
        _store = HistoryStore()
    return _store
