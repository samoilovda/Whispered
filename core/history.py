"""
Whispered – Transcription History Store
SQLite-backed persistence for past transcription results.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Optional

from core.logger import get_logger

logger = get_logger(__name__)

# Placed next to the models directory, inside the Whispered data dir
_DB_PATH = Path.home() / ".whisper-fedora" / "history.db"

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


class HistoryRecord:
    """Lightweight metadata record returned by list().

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
    """

    def __init__(self, db_path: Path = _DB_PATH):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------ init

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(_CREATE_SQL)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row   # named-column access
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
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
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
            return cur.rowcount

    def search(self, text: str, limit: int = 100) -> list[HistoryRecord]:
        """Simple LIKE search across json_payload; returns metadata rows."""
        pattern = f"%{text}%"
        sql = """
            SELECT id, created_at, source_path, source_name, duration, language, model,
                   substr(json_payload, 1, 300) AS preview
            FROM transcripts
            WHERE json_payload LIKE ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (pattern, limit)).fetchall()
        return [HistoryRecord(r) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]


# Module-level singleton (lazy)
_store: Optional[HistoryStore] = None


def get_history_store() -> HistoryStore:
    global _store
    if _store is None:
        _store = HistoryStore()
    return _store
