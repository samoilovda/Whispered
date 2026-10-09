"""Best-effort provenance records for files a user saves or exports.

The generated file is already safely on disk when these run, so a manifest
failure is logged and never turned into a reported failure. Panels call
:func:`record_export` instead of each assembling an ``Artifact`` by hand;
the step registry shares :func:`save_best_effort`.
"""

from __future__ import annotations

from typing import Any, Sequence

from core.logger import get_logger
from domain.artifact import Artifact
from domain.artifact_provenance import source_fingerprint, transcript_revision

logger = get_logger(__name__)


def save_best_effort(artifact: Artifact) -> bool:
    """Write *artifact*'s manifest; return False (and log) on any failure."""
    try:
        from infrastructure.persistence import artifact_store

        artifact_store.save(artifact)
        return True
    except Exception as exc:  # noqa: BLE001 - see module docstring
        logger.warning(
            "Failed to write %s artifact manifest for %s: %s",
            artifact.type, artifact.path, exc,
        )
        return False


def record_export(
    *,
    record_id: "str | int | None",
    source_path: "str | None",
    segments: Sequence[Any],
    language: "str | None",
    type: str,
    path: Any,
    provider: str = "",
    model: str = "",
    prompt_version: str = "",
) -> bool:
    """Record which record/source/transcript revision produced *path*."""
    try:
        artifact = Artifact(
            record_id=str(record_id) if record_id is not None else "unsaved",
            source_hash=source_fingerprint(source_path),
            source_path=source_path or "",
            transcript_revision=transcript_revision(segments, language or ""),
            type=type,
            path=str(path),
            provider=provider,
            model=model,
            prompt_version=prompt_version,
        )
    except Exception as exc:  # noqa: BLE001 - see module docstring
        logger.warning("Failed to build %s artifact for %s: %s", type, path, exc)
        return False
    return save_best_effort(artifact)
