#!/usr/bin/env python3
"""Benchmark HistoryStore open time on a realistic-size database.

Records the before/after numbers R13 asked for
(docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md): opening a ~5000-row history.db
used to run a full FTS rebuild on every launch; it now only rebuilds on
first creation or explicit repair_fts(). "Before" is simulated by forcing
the old always-rebuild behavior for comparison.

Usage:
    python tools/bench_history_open.py [--rows 5000]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domain.transcription import Segment, TranscriptionResult  # noqa: E402


def _make_result(index: int) -> TranscriptionResult:
    return TranscriptionResult(
        segments=[
            Segment(start=0.0, end=1.0, text=f"benchmark row {index} hello world"),
            Segment(start=1.0, end=2.0, text="second segment with more searchable text"),
        ],
        language="en",
        duration=2.0,
    )


def _populate(db_path: Path, rows: int) -> None:
    from core.history import HistoryStore

    store = HistoryStore(db_path=db_path)
    for i in range(rows):
        store.add(_make_result(i), source_path=f"/fake/audio_{i}.wav", model="tiny")


def _time_open(db_path: Path) -> float:
    # Fresh import of HistoryStore per call avoids any module-level caching
    # skewing repeated measurements within one process.
    import importlib

    import core.history as history_module
    importlib.reload(history_module)

    start = time.perf_counter()
    history_module.HistoryStore(db_path=db_path)
    return time.perf_counter() - start


def _time_open_with_forced_rebuild(db_path: Path) -> float:
    """Simulate the pre-R13 behavior: rebuild on every open."""
    import sqlite3

    start = time.perf_counter()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("INSERT INTO transcripts_fts(transcripts_fts) VALUES ('rebuild')")
        conn.commit()
    finally:
        conn.close()
    return time.perf_counter() - start


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=5000)
    args = parser.parse_args()

    tmp_dir = Path(tempfile.mkdtemp(prefix="whispered-bench-"))
    db_path = tmp_dir / "history.db"
    try:
        print(f"Populating {args.rows} rows...")
        _populate(db_path, args.rows)

        after_ms = _time_open(db_path) * 1000
        before_ms = _time_open_with_forced_rebuild(db_path) * 1000

        print(f"\nDatabase: {args.rows} rows, {db_path.stat().st_size / 1024:.0f} KiB")
        print(f"After  (R13 fast path, no rebuild): {after_ms:.1f} ms")
        print(f"Before (forced rebuild, simulated):  {before_ms:.1f} ms")
        if after_ms > 0:
            print(f"Speedup: {before_ms / after_ms:.1f}x")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
