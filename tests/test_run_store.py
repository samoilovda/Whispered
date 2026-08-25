"""Unit tests for application/run_store.py and core/history.py's v4
migration (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B3).
"""

from __future__ import annotations

import sqlite3

import pytest

from application.job_engine import JobRun
from application.run_store import (
    StoredRun,
    apply_stored_outcomes,
    load_latest_run,
    load_latest_runs,
    load_run,
    load_runs_for_record,
    save_run,
)
from core.history import HistoryStore
from domain.job import JobSpec, StepOutcome, StepSpec, StepStatus


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "history.sqlite3"
    HistoryStore(db_path=path)  # runs every migration, including v4
    return path


def _spec(**kwargs) -> JobSpec:
    return JobSpec(name="test", **kwargs)


# ------------------------------------------------------------------ save/load round trip

def test_save_then_load_round_trips_statuses(db_path):
    run = JobRun(spec=_spec(steps=(StepSpec("a"), StepSpec("b"))))
    run.outcomes["a"] = StepOutcome("a", StepStatus.SUCCEEDED, result="ignored")
    run.outcomes["b"] = StepOutcome("b", StepStatus.FAILED, error="LM Studio timed out")

    run_id = save_run(42, "youtube_video", run, status="failed", db_path=db_path)
    stored = load_run(run_id, db_path=db_path)

    assert stored is not None
    assert stored.record_id == 42
    assert stored.recipe == "youtube_video"
    assert stored.status == "failed"
    assert stored.finished_at is not None
    assert stored.outcomes == {
        "a": {"status": "succeeded", "error": ""},
        "b": {"status": "failed", "error": "LM Studio timed out"},
    }


def test_running_status_leaves_finished_at_null(db_path):
    run = JobRun(spec=_spec(steps=(StepSpec("a"),)))
    run.outcomes["a"] = StepOutcome("a", StepStatus.SUCCEEDED)

    run_id = save_run(1, "transcript_only", run, status="running", db_path=db_path)
    stored = load_run(run_id, db_path=db_path)

    assert stored.finished_at is None
    assert stored.status == "running"


def test_passing_run_id_updates_the_same_row_instead_of_inserting(db_path):
    run = JobRun(spec=_spec(steps=(StepSpec("a"), StepSpec("b", depends_on=("a",)))))
    run.outcomes["a"] = StepOutcome("a", StepStatus.SUCCEEDED)

    run_id = save_run(1, "youtube_video", run, status="running", db_path=db_path)

    run.outcomes["b"] = StepOutcome("b", StepStatus.SUCCEEDED)
    same_id = save_run(1, "youtube_video", run, run_id=run_id, status="done", db_path=db_path)

    assert same_id == run_id
    rows = load_runs_for_record(1, db_path=db_path)
    assert len(rows) == 1
    assert rows[0].status == "done"
    assert set(rows[0].outcomes) == {"a", "b"}


def test_load_run_returns_none_for_an_unknown_id(db_path):
    assert load_run(999, db_path=db_path) is None


def test_load_runs_for_record_orders_newest_first(db_path):
    run = JobRun(spec=_spec(steps=(StepSpec("a"),)))
    run.outcomes["a"] = StepOutcome("a", StepStatus.SUCCEEDED)

    first_id = save_run(7, "transcript_only", run, status="done", db_path=db_path)
    second_id = save_run(7, "transcript_only", run, status="done", db_path=db_path)

    rows = load_runs_for_record(7, db_path=db_path)
    assert [row.id for row in rows] == [second_id, first_id]


def test_load_runs_for_record_ignores_other_records(db_path):
    run = JobRun(spec=_spec(steps=(StepSpec("a"),)))
    run.outcomes["a"] = StepOutcome("a", StepStatus.SUCCEEDED)
    save_run(1, "transcript_only", run, status="done", db_path=db_path)
    save_run(2, "transcript_only", run, status="done", db_path=db_path)

    assert len(load_runs_for_record(1, db_path=db_path)) == 1
    assert len(load_runs_for_record(2, db_path=db_path)) == 1


# ------------------------------------------------------------------ load_latest_run

def test_load_latest_run_returns_the_newest_row(db_path):
    run = JobRun(spec=_spec(steps=(StepSpec("a"),)))
    run.outcomes["a"] = StepOutcome("a", StepStatus.SUCCEEDED)

    save_run(3, "youtube_video", run, status="done", db_path=db_path)
    second_id = save_run(3, "youtube_video", run, status="done", db_path=db_path)

    latest = load_latest_run(3, db_path=db_path)
    assert latest is not None
    assert latest.id == second_id


def test_load_latest_run_returns_none_for_a_record_with_no_run(db_path):
    assert load_latest_run(999, db_path=db_path) is None


def test_load_latest_runs_batches_one_row_per_record(db_path):
    """The Library reads a whole page at once (B8) — one query, newest
    run per record, absent for records that never had one."""
    run = JobRun(spec=_spec(steps=(StepSpec("a"),)))
    run.outcomes["a"] = StepOutcome("a", StepStatus.SUCCEEDED)

    save_run(1, "youtube_video", run, status="done", db_path=db_path)
    newest_for_1 = save_run(1, "book", run, status="done", db_path=db_path)
    newest_for_2 = save_run(2, "meeting_notes", run, status="failed", db_path=db_path)

    found = load_latest_runs([1, 2, 3], db_path=db_path)

    assert set(found) == {"1", "2"}          # 3 has no run at all
    assert found["1"].id == newest_for_1
    assert found["1"].recipe == "book"       # the newer of record 1's two
    assert found["2"].id == newest_for_2
    assert found["2"].status == "failed"


def test_load_latest_runs_matches_load_latest_run_per_record(db_path):
    run = JobRun(spec=_spec(steps=(StepSpec("a"),)))
    run.outcomes["a"] = StepOutcome("a", StepStatus.FAILED, error="boom")
    for record_id in (10, 11, 12):
        save_run(record_id, "transcript_only", run, status="failed", db_path=db_path)

    batched = load_latest_runs([10, 11, 12], db_path=db_path)

    for record_id in (10, 11, 12):
        one = load_latest_run(record_id, db_path=db_path)
        assert batched[str(record_id)] == one


def test_load_latest_runs_with_no_record_ids_touches_no_database(db_path):
    assert load_latest_runs([], db_path=db_path) == {}


def test_load_latest_runs_handles_more_records_than_one_sql_chunk(db_path):
    """Chunked under SQLITE_MAX_VARIABLE_NUMBER — a page bigger than one
    chunk must still come back complete."""
    run = JobRun(spec=_spec(steps=(StepSpec("a"),)))
    run.outcomes["a"] = StepOutcome("a", StepStatus.SUCCEEDED)
    ids = list(range(100, 100 + 950))
    for record_id in ids:
        save_run(record_id, "transcript_only", run, status="done", db_path=db_path)

    found = load_latest_runs(ids, db_path=db_path)

    assert len(found) == len(ids)


# ------------------------------------------------------------------ apply_stored_outcomes

def test_apply_stored_outcomes_resumes_a_job_run():
    stored = StoredRun(
        id=1, record_id=1, recipe="youtube_video", started_at="now",
        finished_at=None, status="running",
        outcomes={
            "a": {"status": "succeeded", "error": ""},
            "b": {"status": "failed", "error": "boom"},
        },
    )
    run = JobRun(spec=_spec(steps=(StepSpec("a"), StepSpec("b"))))
    apply_stored_outcomes(run, stored)

    assert run.outcomes["a"].status == StepStatus.SUCCEEDED
    assert run.outcomes["a"].result is None  # see module docstring
    assert run.outcomes["b"].status == StepStatus.FAILED
    assert run.outcomes["b"].error == "boom"


def test_apply_stored_outcomes_skips_unknown_status_without_raising():
    stored = StoredRun(
        id=1, record_id=1, recipe="x", started_at="now", finished_at=None,
        status="running", outcomes={"a": {"status": "quantum_superposition", "error": ""}},
    )
    run = JobRun(spec=_spec(steps=(StepSpec("a"),)))
    apply_stored_outcomes(run, stored)

    assert "a" not in run.outcomes


def test_resumed_job_run_causes_job_engine_to_skip_the_resolved_step():
    """The actual point of all this: a JobRunner built with a resumed
    JobRun must not re-run a step apply_stored_outcomes marked done."""
    from application.job_engine import JobEngine

    stored = StoredRun(
        id=1, record_id=1, recipe="x", started_at="now", finished_at=None,
        status="running", outcomes={"a": {"status": "succeeded", "error": ""}},
    )
    spec = _spec(steps=(StepSpec("a"), StepSpec("b", depends_on=("a",))))
    run = JobRun(spec=spec)
    apply_stored_outcomes(run, stored)

    calls = []
    engine = JobEngine()
    engine.run(
        spec,
        {"a": lambda: calls.append("a"), "b": lambda: calls.append("b") or "b-result"},
        run_state=run,
    )

    assert calls == ["b"]
    assert run.outcomes["b"].status == StepStatus.SUCCEEDED


# ------------------------------------------------------------------ malformed data

def test_row_with_malformed_outcomes_json_logs_and_returns_empty_dict(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO job_runs (record_id, recipe, started_at, status, outcomes_json) "
            "VALUES (?, ?, ?, ?, ?)",
            ("1", "x", "now", "running", "{not valid json"),
        )
        conn.commit()
        run_id = conn.execute("SELECT id FROM job_runs").fetchone()[0]

    stored = load_run(run_id, db_path=db_path)
    assert stored.outcomes == {}


# ------------------------------------------------------------------ migration itself

def test_v3_database_migrates_to_v4_without_losing_existing_rows(tmp_path):
    """A database written by the pre-B3 code (schema v3: transcripts +
    artifacts + source_kind, no job_runs) must gain job_runs and keep
    every existing transcript row."""
    path = tmp_path / "history.sqlite3"
    with sqlite3.connect(str(path)) as conn:
        conn.executescript("""
            CREATE TABLE transcripts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT    NOT NULL,
                source_path TEXT    NOT NULL,
                source_name TEXT    NOT NULL,
                duration    REAL    NOT NULL DEFAULT 0,
                language    TEXT    NOT NULL DEFAULT '',
                model       TEXT    NOT NULL DEFAULT '',
                json_payload TEXT   NOT NULL,
                artifacts TEXT,
                source_kind TEXT NOT NULL DEFAULT 'file'
            );
        """)
        conn.execute(
            "INSERT INTO transcripts (created_at, source_path, source_name, json_payload) "
            "VALUES ('now', '/x.mp3', 'x.mp3', '{}')"
        )
        conn.execute("PRAGMA user_version = 3")
        conn.commit()

    store = HistoryStore(db_path=path)
    with sqlite3.connect(str(path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        row_count = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]

    assert version == 4
    assert "job_runs" in tables
    assert row_count == 1
    assert store.count() == 1


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "history.sqlite3"
    HistoryStore(db_path=path)
    # Constructing a second HistoryStore against the same file re-runs
    # _migrate(); it must be a no-op the second time, not raise on
    # "table already exists".
    store2 = HistoryStore(db_path=path)
    with sqlite3.connect(str(path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 4
    assert store2.count() == 0
