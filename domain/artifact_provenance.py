"""Shared helpers for building an Artifact's provenance fields.

See docs/archive/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R5-full step 3: every
generator that gets migrated onto Artifact needs the same two inputs — a
stable identifier for the source media file, and a stable identifier for
the transcript content actually used — so this is factored out once
instead of reimplemented per generator (Cover is the first; article,
YouTube, insights, and book are meant to follow the same pattern).
"""

from __future__ import annotations

import hashlib
import json
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


def transcript_revision(
    segments: Iterable[Any], language: str = "", speaker_names: Optional[dict] = None,
) -> str:
    """Versioned identity of text, timing, labels, words and display names.

    Canonical JSON supports both persisted dictionaries and fresh DTOs.
    Numeric times are normalized so reloading does not create a new version.
    """
    def value(item: Any, name: str, default: Any = None) -> Any:
        return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)

    parts = []
    for seg in segments:
        parts.append({
            "text": value(seg, "text", "") or "",
            "start": float(value(seg, "start", 0) or 0),
            "end": float(value(seg, "end", 0) or 0),
            "speaker": value(seg, "speaker") or "",
            "words": [
                {"start": float(value(word, "start", 0) or 0),
                 "end": float(value(word, "end", 0) or 0),
                 "text": value(word, "text", "") or ""}
                for word in value(seg, "words", []) or []
            ],
        })
    payload = json.dumps(
        {"segments": parts, "language": language, "speaker_names": speaker_names or {}},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    # The prefix deliberately invalidates text-only keys from older builds.
    return "v2:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
