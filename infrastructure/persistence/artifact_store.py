"""Reads and writes an Artifact's provenance manifest next to its file.

See docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R5-full/R8-pre.

Manifest filename convention: ``<artifact path>.manifest.json``. Written via
temp file + os.replace in the same directory, matching the atomic-write
pattern already used for Cover export and Recorder output — a crash or
Cancel mid-write must never leave a corrupt or partial manifest that a
cache lookup could misread as valid.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from domain.artifact import Artifact


def manifest_path_for(artifact_path: str | Path) -> Path:
    return Path(str(artifact_path) + ".manifest.json")


def save(artifact: Artifact) -> Path:
    """Write *artifact*'s manifest atomically next to its file.

    Does not create or touch the artifact file itself — callers write that
    separately (typically already atomically) and then record its
    provenance here.
    """
    target = manifest_path_for(artifact.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(artifact.to_dict(), fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def load(artifact_path: str | Path) -> Optional[Artifact]:
    """Read back the manifest for *artifact_path*.

    Returns ``None`` if it doesn't exist or is corrupt — a bad manifest
    must read as "no provenance on record", not raise and break whatever
    is doing the cache lookup.
    """
    manifest = manifest_path_for(artifact_path)
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return Artifact.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def is_cache_valid(artifact_path: str | Path, expected: Artifact) -> bool:
    """True if *artifact_path* exists, has a manifest, and that manifest's
    cache key matches *expected*'s — i.e. regenerating would produce the
    same output, so the existing file can be reused."""
    if not Path(artifact_path).exists():
        return False
    existing = load(artifact_path)
    if existing is None:
        return False
    return existing.cache_key() == expected.cache_key()
