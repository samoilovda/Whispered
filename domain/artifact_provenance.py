"""Shared helpers for building an Artifact's provenance fields.

See docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R5-full step 3: every
generator that gets migrated onto Artifact needs the same two inputs — a
stable identifier for the source media file, and a stable identifier for
the transcript content actually used — so this is factored out once
instead of reimplemented per generator (Cover is the first; article,
YouTube, insights, and book are meant to follow the same pattern).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Optional


def source_fingerprint(source_path: Optional[str]) -> str:
    """A cheap, stable identifier for a source media file.

    Deliberately not a content hash of the whole file — audio/video
    sources can be gigabytes, and re-reading them on every export just to
    label provenance isn't worth the cost (that full-content-integrity
    concern belongs to R2's model-download verification, a different
    problem). Path + size + mtime changes whenever the file is actually
    replaced, which is what provenance needs to detect; it is not meant
    to survive the file being copied or moved.
    """
    if not source_path:
        return "no-source"
    path = Path(source_path)
    try:
        stat = path.stat()
        fingerprint_input = f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        fingerprint_input = str(path)
    return hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()[:16]


def transcript_revision(segments: Iterable[Any], language: str = "") -> str:
    """A stable identifier for a transcript's actual content.

    Changes whenever the text a generator would read from changes (an
    edit, a re-transcription) — exactly what "revision" needs to mean for
    provenance/cache purposes without the codebase having a real
    versioning system for transcripts. Takes segments directly (not a
    full TranscriptionResult) so callers that only ever see segments —
    like the panels that already receive them via
    DocumentSession/set_segments — don't need to construct one just for
    this; each segment may be a dict (from a reloaded history record) or
    a Segment dataclass instance (from a fresh transcription), matching
    the duck-typing already used in core.insights_worker._build_prompt_text.
    """
    parts = []
    for seg in segments:
        text = seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", "")
        parts.append(text or "")
    digest = hashlib.sha256("\n".join(parts).encode("utf-8"))
    digest.update(language.encode("utf-8"))
    return digest.hexdigest()[:16]
