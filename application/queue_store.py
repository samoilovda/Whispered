"""Reading and writing the persistent recipe queue (UX-14 / T14).

Qt-free glue between domain/queue.py and the history database. Nothing
here starts any processing: queueing, restoring after a restart and
retrying only change stored state — the queue runs when the user starts it.
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable, Optional

from core.logger import get_logger
from domain import queue as q

logger = get_logger(__name__)


def _store():
    from core.history import get_history_store

    return get_history_store()


def new_batch_id() -> str:
    return uuid.uuid4().hex[:12]


def make_snapshot(recipe: Any, transcription: dict) -> q.QueueSnapshot:
    """Freeze *recipe* (a ``domain.recipe.Recipe``) and the transcription
    settings (model, language, translate, n_threads, enable_diarization,
    use_gpu) as they are now."""
    return q.QueueSnapshot(recipe.to_dict(), dict(transcription))


def enqueue(paths: Iterable[str], snapshot: q.QueueSnapshot,
            batch_id: Optional[str] = None) -> dict[str, int]:
    """Persist *paths* as pending items with *snapshot*. Returns
    ``{path: item_id}`` for the ones actually added — a path already
    waiting in the queue is skipped, so a watch-folder event seen twice or
    a re-dropped file does not queue twice."""
    batch = batch_id or new_batch_id()
    added: dict[str, int] = {}
    store = _store()
    for path in paths:
        item_id = store.queue_enqueue(batch, path, snapshot.to_json())
        if item_id is not None:
            added[path] = item_id
    return added


def item_from_row(row: dict) -> q.QueueItem:
    return q.QueueItem(
        id=row["id"], batch_id=row["batch_id"], position=row["position"],
        source_path=row["source_path"],
        snapshot=q.QueueSnapshot.from_json(row["snapshot_json"]),
        state=row["state"], stage=row["stage"], record_id=row["record_id"],
        run_id=row["run_id"], error=row["error"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def load_items(*, unfinished_only: bool = False) -> list[q.QueueItem]:
    return [item_from_row(row) for row in _store().queue_list(unfinished_only=unfinished_only)]


def load_item(item_id: int) -> Optional[q.QueueItem]:
    row = _store().queue_get(item_id)
    return item_from_row(row) if row is not None else None


def set_state(item_id: int, state: str, **fields: Any) -> None:
    """Move an item to *state*; extra fields (``stage``, ``record_id``,
    ``run_id``, ``error``) are written with it."""
    if state not in q.STATES:
        raise ValueError(f"unknown queue state {state!r}")
    _store().queue_update(item_id, state=state, **fields)


def mark_interrupted() -> int:
    """At startup: what a crash left ``running`` is now ``interrupted``."""
    return _store().queue_mark_interrupted()


def retry(item_id: int) -> bool:
    """Put a failed / cancelled / interrupted item back to pending. A record
    it already produced is kept: the queue then runs only the steps that
    didn't finish (it never transcribes the same file twice)."""
    item = load_item(item_id)
    if item is None or not item.retryable:
        return False
    set_state(item_id, q.PENDING, stage="", error="")
    return True


def reorder(item_ids: list[int]) -> None:
    _store().queue_reorder(item_ids)


def remove(item_id: int) -> None:
    _store().queue_remove(item_id)


def clear_finished() -> int:
    return _store().queue_clear_finished()
