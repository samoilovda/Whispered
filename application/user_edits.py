"""User edits to a generated step result, kept apart from the result itself.

A step's output file (e.g. ``youtube_package.json``) and its provenance
manifest describe what the *model* produced; the step's cache-skip compares
against them. Writing the user's corrections into that file would make the
cache treat hand edits as model output. Edits therefore live in an overlay
file next to it — ``youtube_package.user.json`` — holding only the keys the
user changed, and are merged in for display, copying and publishing.

Each edited key ``k`` is stored with ``k + "_base"``: a fingerprint of the
model's value the edit was made against. When the step later produces a
different value, the UI can tell the user the model has changed underneath
their edit instead of silently keeping or dropping it.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)

OVERLAY_SUFFIX = ".user.json"
BASE_SUFFIX = "_base"


def overlay_path(result_path: Path) -> Path:
    """``<dir>/youtube_package.json`` → ``<dir>/youtube_package.user.json``."""
    return result_path.with_name(result_path.stem + OVERLAY_SUFFIX)


def fingerprint(value: Any) -> str:
    """Stable short hash of a JSON-serialisable value."""
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def load_overlay(path: Path) -> dict[str, Any]:
    """The overlay at *path*, or ``{}`` if it is missing or unreadable —
    a broken overlay must never hide the model's result."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable user-edit overlay %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def save_overlay(path: Path, data: dict[str, Any]) -> None:
    """Write *data* atomically; an empty overlay removes the file."""
    if not data:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def set_edit(overlay: dict[str, Any], key: str, value: Any, model_value: Any) -> dict[str, Any]:
    """Return a copy of *overlay* with *key* set to *value*, recorded as made
    against *model_value*. An edit equal to the model's value is no edit at
    all and removes *key* instead."""
    updated = dict(overlay)
    if value == model_value:
        updated.pop(key, None)
        updated.pop(key + BASE_SUFFIX, None)
    else:
        updated[key] = value
        updated[key + BASE_SUFFIX] = fingerprint(model_value)
    return updated


def drop_edit(overlay: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a copy of *overlay* without the user's edit of *key*."""
    updated = dict(overlay)
    updated.pop(key, None)
    updated.pop(key + BASE_SUFFIX, None)
    return updated


def rebase_edit(overlay: dict[str, Any], key: str, model_value: Any) -> dict[str, Any]:
    """Return a copy of *overlay* whose edit of *key* now counts as made
    against *model_value* — the user chose to keep it over a newer result."""
    updated = dict(overlay)
    if key in updated:
        updated[key + BASE_SUFFIX] = fingerprint(model_value)
    return updated


def is_stale(overlay: dict[str, Any], key: str, model_value: Any) -> bool:
    """True when *key* is edited and the model's value has changed since."""
    if key not in overlay:
        return False
    return overlay.get(key + BASE_SUFFIX) != fingerprint(model_value)
