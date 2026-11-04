"""History records written as a whole: a new transcript with its first
version, and the badges (artifact types) a record carries.

UI code calls these instead of composing several history-store writes
itself; single reads and edits (a title, a bookmark) stay direct calls.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Collection, Iterable, Optional

from core.logger import get_logger

logger = get_logger(__name__)

# An artifact folder is ``<stem>-<record id>`` (core.paths.artifact_dir).
_FOLDER_ID_RE = re.compile(r"-(\d+)$")


def folder_record_id(folder: Path) -> Optional[int]:
    """The history record id an artifact folder belongs to, ``None`` for a
    folder from before ids were part of the name."""
    match = _FOLDER_ID_RE.search(folder.name)
    return int(match.group(1)) if match else None


def live_record_ids() -> Optional[set[int]]:
    """Ids of the records that still exist, ``None`` when the history can't
    be read (callers then filter nothing rather than everything)."""
    from core.history import get_history_store

    try:
        return get_history_store().record_ids()
    except Exception as exc:  # noqa: BLE001 - a broken store must not stop the CLI tools
        logger.warning("Could not read the history's record ids: %s", exc)
        return None


def is_live(record_id: Optional[int], live_ids: Optional[Collection[int]]) -> bool:
    """False only for a record known to be deleted: *record_id* is set,
    the live ids could be read, and it is not among them."""
    return record_id is None or live_ids is None or record_id in live_ids


def withdraw_uploads(record_ids: Optional[Collection[int]], root: Optional[Path] = None) -> int:
    """Withdraw the approved YouTube uploads of *record_ids* (all records
    when ``None``): each ``youtube_upload.queued.json`` is renamed to
    ``youtube_upload.withdrawn.json``, so no queue run, CLI or scheduled
    job publishes a deleted record. Returns how many were withdrawn."""
    from application.youtube_autoupload import QUEUE_FILE, WITHDRAWN_FILE, load_queued
    from core.paths import output_dir

    base = root if root is not None else output_dir()
    withdrawn = 0
    for path in sorted(base.glob(f"*/{QUEUE_FILE}")):
        item = load_queued(path)
        record_id = item.record_id if item is not None and item.record_id is not None \
            else folder_record_id(path.parent)
        if record_ids is not None and record_id not in record_ids:
            continue
        try:
            path.replace(path.with_name(WITHDRAWN_FILE))
            withdrawn += 1
        except OSError as exc:
            logger.warning("Could not withdraw the queued upload %s: %s", path, exc)
    return withdrawn


def delete_record(record_id: int) -> bool:
    """Delete one history record and withdraw its approved upload. Raises
    what the store raises; returns whether the row existed."""
    from core.history import get_history_store

    existed = get_history_store().delete(record_id)
    withdraw_uploads({record_id})
    return existed


def clear_records() -> int:
    """Delete every history record and withdraw every approved upload.
    Raises what the store raises; returns the number of records removed."""
    from core.history import get_history_store

    removed = get_history_store().clear()
    withdraw_uploads(None)
    return removed


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
    if not cfg.history_enabled:
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


def save_record_version(record_id: Any, result: Any) -> bool:
    """Make *result* the record's current transcript and save it as a new
    version (a step such as speaker detection produced a new transcript
    for an existing record). The result's ``speaker_names`` — the user's
    renames — are stored with it, and the record keeps its badges (the
    generated files are still there). ``False`` (and logged) when the
    write failed or there is no record."""
    if record_id is None:
        return False
    from config import get_config
    from core.history import get_history_store

    speaker_names = getattr(result, "speaker_names", {}) or {}
    try:
        store = get_history_store()
        badges = (store.get_record(record_id) or {}).get("artifacts") or []
        store.update_result(record_id, result, speaker_names=speaker_names)
        if badges:
            store.set_artifacts(record_id, badges)
        store.save_current_revision(
            record_id, result, speaker_names,
            keep=get_config().transcript_revisions_kept, include_speakers=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - the transcript stays open on screen
        logger.warning("Failed to save a new version of record %s: %s", record_id, exc)
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
