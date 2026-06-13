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
from typing import Any, Generator, Optional

from core.logger import get_logger
from core.paths import data_dir

logger = get_logger(__name__)

_DB_PATH = data_dir() / "history.db"

_CREATE_SQL = """
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
"""

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
    except Exception:
        return {}


def _fts_query(text: str) -> str:
    """Build a safe FTS5 query from user input.

    Appends '*' for prefix matching; escapes double-quotes.
    """
    # FTS5 phrase queries use double-quotes; escape any stray ones.
    clean = text.replace('"', '""').strip()
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

    __slots__ = ("id", "created_at", "source_path", "source_name",
                 "duration", "language", "model", "preview")

    def __init__(self, row: sqlite3.Row):
        self.id          = row["id"]
        self.created_at  = row["created_at"]
        self.source_path = row["source_path"]
        self.source_name = row["source_name"]
        self.duration    = row["duration"]
        self.language    = row["language"]
        self.model       = row["model"]
        self.preview     = row["preview"]


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
            conn.executescript(_CREATE_SQL)
        self._init_fts()

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
        conn.execute("PRAGMA journal_mode=WAL")
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
            speaker_names: dict | None = None) -> int:
        """Persist a TranscriptionResult; returns the new row id."""
        payload = _result_to_payload(result, model, speaker_names)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        source_name = Path(source_path).name
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO transcripts
                   (created_at, source_path, source_name, duration, language, model, json_payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (now, source_path, source_name, result.duration,
                 result.language, model, payload),
            )
            return cur.lastrowid

    def list(self, limit: int = 200, offset: int = 0) -> list[HistoryRecord]:
        """Return lightweight metadata rows, newest first."""
        sql = """
            SELECT id, created_at, source_path, source_name, duration, language, model,
                   substr(json_payload, 1, 300) AS preview
            FROM transcripts
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (limit, offset)).fetchall()
        return [HistoryRecord(r) for r in rows]

    def get(self, record_id: int) -> Optional[dict]:
        """Return the full JSON payload dict for a given id, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT json_payload FROM transcripts WHERE id = ?", (record_id,)
            ).fetchone()
        if row is None:
            return None
        return _payload_to_dict(row["json_payload"])

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
                except Exception:
                    pass
            return cur.rowcount

    def search(self, text: str, limit: int = 100) -> list[HistoryRecord]:
        """Search across transcripts; uses FTS5 when available, else LIKE."""
        if not text.strip():
            return self.list(limit=limit)
        if self._fts_available:
            return self._search_fts(text, limit)
        return self._search_like(text, limit)

    def _search_fts(self, text: str, limit: int) -> list[HistoryRecord]:
        query = _fts_query(text)
        # snippet() highlights column 1 (json_payload); column 0 is source_name
        sql = """
            SELECT t.id, t.created_at, t.source_path, t.source_name,
                   t.duration, t.language, t.model,
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

    def _search_like(self, text: str, limit: int) -> list[HistoryRecord]:
        pattern = f"%{text}%"
        sql = """
            SELECT id, created_at, source_path, source_name, duration, language, model,
                   substr(json_payload, 1, 300) AS preview
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
