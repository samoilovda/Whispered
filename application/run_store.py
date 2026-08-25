"""Persists JobRun state to history.sqlite3's job_runs table (schema v4 —
see core/history.py's _v4_add_job_runs_table and
docs/UI_REDESIGN_PLAN_2026-09.ru.md, B3).

Without this, "retry one step" (JobRun.reset_step()) only works while the
app stays open, and the Library can't show which steps of a run
succeeded/failed for a record whose window has long since closed.

Deliberately narrow: outcomes are serialized as
``{name: {status, error}}`` — a StepOutcome's ``result`` is never stored
here. A step's real output already lives in the artifact file on disk
(infrastructure/persistence/artifact_store.py); re-hydrating an arbitrary
Python object (a ProcessingResult, a GenerationResult, ...) from SQLite
isn't a problem this module takes on. A JobRun resumed via
``apply_stored_outcomes`` has every restored StepOutcome.result as
``None`` — a runner that needs a completed dependency's actual output
(not just "did it succeed") reads the artifact file, same as any other
caller of a finished step's result would.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from application.job_engine import JobRun
from core.logger import get_logger
from domain.job import StepOutcome, StepStatus

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db_path() -> Path:
    # Ensures job_runs exists (HistoryStore.__init__ runs every pending
    # migration) even if no HistoryStore was otherwise constructed yet,
    # then hands back the same file — job_runs lives in history.sqlite3,
    # not a separate database.
    from core.history import get_history_store

    return get_history_store()._db_path


@contextmanager
def _connect(db_path: Optional[Path] = None) -> Generator[sqlite3.Connection, None, None]:
    path = db_path if db_path is not None else _db_path()
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@dataclass(frozen=True)
class StoredRun:
    """One job_runs row, as read back.

    Not a JobRun (which needs a live JobSpec with real StepSpecs — see
    ``apply_stored_outcomes`` for how to fold this back into one) — just
    enough to answer "what happened" without re-resolving a JobSpec.
    """

    id: int
    record_id: int
    recipe: str
    started_at: str
    finished_at: Optional[str]
    status: str
    outcomes: dict  # name -> {"status": str, "error": str}


def _serialize_outcomes(outcomes: dict) -> str:
    return json.dumps({
        name: {"status": str(outcome.status.value), "error": outcome.error}
        for name, outcome in outcomes.items()
    })


def _row_to_stored_run(row: sqlite3.Row) -> StoredRun:
    try:
        outcomes = json.loads(row["outcomes_json"])
    except (json.JSONDecodeError, TypeError):
        logger.warning("Malformed outcomes_json for job_runs row %s", row["id"])
        outcomes = {}
    return StoredRun(
        id=row["id"],
        record_id=row["record_id"],
        recipe=row["recipe"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        outcomes=outcomes,
    )


def save_run(
    record_id: "int | str",
    recipe: str,
    run: JobRun,
    *,
    run_id: Optional[int] = None,
    status: str = "running",
    db_path: Optional[Path] = None,
) -> int:
    """Insert or update one job_runs row for *run*'s current outcomes.

    Pass the id this returns back in as *run_id* on a later call (e.g.
    once more steps resolve, or the whole run finishes) to update that
    same row instead of inserting a new one each time. *status* is the
    caller's own summary of the run as a whole (JobRun itself has no such
    concept, only per-step outcomes) — typically "running" while steps
    are still resolving and "done"/"failed" once ``job_finished`` fires.
    """
    outcomes_json = _serialize_outcomes(run.outcomes)
    finished_at = _now_iso() if status != "running" else None

    with _connect(db_path) as conn:
        if run_id is None:
            cursor = conn.execute(
                "INSERT INTO job_runs "
                "(record_id, recipe, started_at, finished_at, status, outcomes_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(record_id), recipe, _now_iso(), finished_at, status, outcomes_json),
            )
            assert cursor.lastrowid is not None  # AUTOINCREMENT always sets one on INSERT
            return cursor.lastrowid
        conn.execute(
            "UPDATE job_runs SET status = ?, finished_at = ?, outcomes_json = ? "
            "WHERE id = ?",
            (status, finished_at, outcomes_json, run_id),
        )
        return run_id


def load_run(run_id: int, *, db_path: Optional[Path] = None) -> Optional[StoredRun]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, record_id, recipe, started_at, finished_at, status, outcomes_json "
            "FROM job_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    return _row_to_stored_run(row) if row is not None else None


def load_runs_for_record(
    record_id: "int | str", *, db_path: Optional[Path] = None
) -> "list[StoredRun]":
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, record_id, recipe, started_at, finished_at, status, outcomes_json "
            "FROM job_runs WHERE record_id = ? ORDER BY id DESC",
            (str(record_id),),
        ).fetchall()
    return [_row_to_stored_run(row) for row in rows]


def apply_stored_outcomes(run: JobRun, stored: StoredRun) -> None:
    """Populate *run*.outcomes from *stored*'s serialized snapshot — how a
    JobRun resumes across a restart. Every restored StepOutcome.result is
    ``None`` (see module docstring); an unrecognized status string is
    skipped rather than raising, so a row written by a newer build with a
    status this one doesn't know about doesn't break resume.
    """
    for name, entry in stored.outcomes.items():
        try:
            status = StepStatus(entry.get("status"))
        except ValueError:
            logger.warning(
                "Unknown step status %r for %r in stored run %d — skipping",
                entry.get("status"), name, stored.id,
            )
            continue
        run.outcomes[name] = StepOutcome(
            name=name, status=status, error=str(entry.get("error", "")), result=None,
        )
