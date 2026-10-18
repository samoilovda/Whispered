"""Reads and writes an Artifact's provenance manifest next to its file.

See docs/archive/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R5-full/R8-pre.

Manifest filename convention: ``<artifact path>.manifest.json``. Written via
temp file + os.replace in the same directory, matching the atomic-write
pattern already used for Cover export and Recorder output — a crash or
Cancel mid-write must never leave a corrupt or partial manifest that a
cache lookup could misread as valid.
"""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

from domain.artifact import Artifact
from domain.cancellation import check_cancelled


# Keep local writers/readers from observing a half-committed pair. Across
# processes/crashes the content digest makes a mismatched pair a cache miss.
_COMMIT_LOCK = threading.RLock()


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def commit(
    artifact: Artifact, writer: Callable[[Path], None], *,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> None:
    """Stage a complete output, then replace it and record its digest.

    A failed/cancelled writer leaves the old output intact. If manifest
    replacement fails after output replacement, its old digest cannot
    validate the new output. Legacy manifests without a digest are misses.
    """
    check_cancelled(is_cancelled)
    target = Path(artifact.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=target.suffix, dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        writer(temporary)
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        content_sha256 = _digest(temporary)
        with _COMMIT_LOCK:
            check_cancelled(is_cancelled)
            os.replace(temporary, target)
            save(artifact, content_sha256=content_sha256)
    finally:
        temporary.unlink(missing_ok=True)


def write_text(
    artifact: Artifact, text: str, *, is_cancelled: Callable[[], bool] = lambda: False,
) -> None:
    """Commit one UTF-8 artifact without truncating a previous output."""
    def write(path: Path) -> None:
        path.write_text(text, encoding="utf-8")

    commit(artifact, write, is_cancelled=is_cancelled)


def manifest_path_for(artifact_path: str | Path) -> Path:
    return Path(str(artifact_path) + ".manifest.json")


def save(artifact: Artifact, *, content_sha256: Optional[str] = None) -> Path:
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
            data = artifact.to_dict()
            output = Path(artifact.path)
            data["content_sha256"] = (
                content_sha256 if content_sha256 is not None
                else _digest(output) if output.is_file() else None
            )
            json.dump(data, fh, ensure_ascii=False, indent=2)
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
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def is_cache_valid(artifact_path: str | Path, expected: Artifact) -> bool:
    """True if *artifact_path* exists, has a manifest, and that manifest's
    cache key matches *expected*'s — i.e. regenerating would produce the
    same output, so the existing file can be reused."""
    with _COMMIT_LOCK:
        try:
            data = json.loads(manifest_path_for(artifact_path).read_text(encoding="utf-8"))
            existing = Artifact.from_dict(data)
            digest = data.get("content_sha256")
            return (
                existing.cache_key() == expected.cache_key()
                and bool(digest)
                and digest == _digest(Path(artifact_path))
            )
        except (OSError, ValueError, KeyError, TypeError):
            return False
