"""The user's own notes about a record (L1, docs/UI_CONCEPT_IMPLEMENTATION_PLAN_2026-10.ru.md).

Typed during a live session or later on the Insights tab, kept as
``notes.md`` in the record's output folder (beside the generated
materials, not inside any of them), indexed for search like a material,
and handed to the Insights step as extra input so the summary reflects
what the user cared about. Qt-free.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from core.logger import get_logger

logger = get_logger(__name__)

NOTES_FILE = "notes.md"


def notes_path(folder: Path) -> Path:
    return Path(folder) / NOTES_FILE


def load_notes(folder: Path) -> str:
    """The notes in *folder*, or "" when there are none or they can't be
    read."""
    try:
        return notes_path(folder).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Could not read notes in %s: %s", folder, exc)
        return ""


def save_notes(folder: Path, text: str, record_id: int | None = None) -> None:
    """Write *text* atomically (blank notes remove the file) and, for a
    saved record, refresh its search index entry."""
    path = notes_path(folder)
    if not text.strip():
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    if isinstance(record_id, int):
        try:
            from core.history import get_history_store

            get_history_store().set_artifact_text(record_id, "notes", str(path), text.strip())
        except Exception as exc:  # noqa: BLE001 - indexing is best-effort
            logger.warning("Failed to index notes of record %s: %s", record_id, exc)


def notes_fingerprint(text: str) -> str:
    """Short hash of the notes — part of the Insights step's cache key,
    so changed notes make the next run regenerate."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]
