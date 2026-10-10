"""History records written as a whole: a new transcript with its first
version, and the badges (artifact types) a record carries.

UI code calls these instead of composing several history-store writes
itself; single reads and edits (a title, a bookmark) stay direct calls.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from core.logger import get_logger

logger = get_logger(__name__)


def save_new_record(
    result: Any,
    *,
    source_path: str,
    model: str,
    speaker_names: Optional[dict] = None,
    source_kind: str = "file",
    source_name: Optional[str] = None,
) -> Optional[int]:
    """Store *result* as a new record and its "as transcribed" version.

    Returns the new record id, or ``None`` when history is turned off or the
    write failed (logged; a transcript on screen is never lost over it).
    """
    from config import get_config
    from core.history import get_history_store

    cfg = get_config()
    if not getattr(cfg, "history_enabled", True):
        return None
    try:
        store = get_history_store()
        record_id = store.add(
            result,
            source_path=source_path,
            model=model,
            speaker_names=speaker_names or {},
            source_kind=source_kind,
            source_name=source_name,
        )
        # The first version is written at once, so a record that is never
        # edited still has a baseline to restore to.
        store.save_current_revision(
            record_id, result, speaker_names or {}, keep=cfg.transcript_revisions_kept,
        )
        return record_id
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("Failed to save history: %s", exc)
        return None


def add_record_badges(record_id: Any, artifact_types: Iterable[str]) -> bool:
    """Add *artifact_types* (plus the transcript) to the record's history
    badges, keeping the ones it already has. Best effort: ``False`` (and
    logged) when there is nothing to add or the write failed."""
    types = set(artifact_types)
    if record_id is None or not types:
        return False
    try:
        from core.history import get_history_store

        store = get_history_store()
        current = store.get_record(record_id) or {}
        artifacts = {"transcript", *types, *current.get("artifacts", [])}
        store.set_artifacts(record_id, sorted(artifacts))
        return True
    except Exception as exc:  # noqa: BLE001 - badges never fail what produced them
        logger.warning("Failed to update the badges of record %s: %s", record_id, exc)
        return False


def finalize_live_record(record_id: int, result: Any) -> bool:
    """A Live session ended: replace its checkpointed record with the
    finished transcript and keep that as the record's first version (the
    checkpoints during the meeting are in-place saves, not versions).
    ``False`` (and logged) when the write failed."""
    from config import get_config
    from core.history import get_history_store

    speaker_names = getattr(result, "speaker_names", {}) or {}
    try:
        store = get_history_store()
        store.update_result(record_id, result, speaker_names=speaker_names)
        store.save_current_revision(
            record_id, result, speaker_names, keep=get_config().transcript_revisions_kept,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - the transcript stays open on screen
        logger.warning("Failed to finalize live transcript history: %s", exc)
        return False
