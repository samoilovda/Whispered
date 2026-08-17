"""Provenance record for a generated artifact (Cover PNG, article draft,
YouTube package, insights, book chapter, ...).

See docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R5-full/R8-pre. Answers, for
any artifact on disk: which transcript revision, which provider/model, and
which prompt version produced it — exactly the cache key a resumable Job
Engine (R8) needs to decide whether an artifact can be reused or must be
regenerated, and what the audit's short-term fix (core.paths.artifact_dir)
could not answer on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Tuple


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Artifact:
    """One generated file plus the inputs that produced it.

    ``record_id`` ties it back to the history row (transcript) it was
    generated from. ``source_hash``/``source_path`` identify the original
    media file. ``type`` is a short tag such as ``"cover"``,
    ``"article_blog"``, ``"youtube_chapters"``, ``"insights_summary"``,
    ``"book"``.
    """

    record_id: str
    source_hash: str
    source_path: str
    transcript_revision: str
    type: str
    path: str
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    created_at: str = field(default_factory=_now_iso)
    extra: Dict[str, Any] = field(default_factory=dict)

    def cache_key(self) -> Tuple[str, str, str, str, str, str]:
        """Inputs that determine whether this artifact can be reused.

        Two Artifacts with an equal ``cache_key()`` were produced from the
        same transcript revision, source, type, provider, model, and
        prompt version — regenerating would produce the same output.
        """
        return (
            self.transcript_revision,
            self.source_hash,
            self.type,
            self.provider,
            self.model,
            self.prompt_version,
        )

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "source_hash": self.source_hash,
            "source_path": self.source_path,
            "transcript_revision": self.transcript_revision,
            "type": self.type,
            "path": self.path,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "created_at": self.created_at,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Artifact":
        return cls(
            record_id=str(data["record_id"]),
            source_hash=str(data["source_hash"]),
            source_path=str(data["source_path"]),
            transcript_revision=str(data["transcript_revision"]),
            type=str(data["type"]),
            path=str(data["path"]),
            provider=str(data.get("provider", "")),
            model=str(data.get("model", "")),
            prompt_version=str(data.get("prompt_version", "")),
            created_at=str(data.get("created_at") or _now_iso()),
            extra=dict(data.get("extra") or {}),
        )
