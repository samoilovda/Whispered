"""Conservative cross-source echo and duplicate detection."""

from __future__ import annotations

import re
import string
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DuplicateDecision:
    """Decision for one final segment submitted to the detector."""

    duplicate: bool
    canonical_id: str | None
    source: str
    overlap_seconds: float = 0.0
    ambiguous: bool = False
    reason: str = "unique"


@dataclass(frozen=True, slots=True)
class _Candidate:
    segment_id: str
    source: str
    segment: Any


class EchoDuplicateDetector:
    """Drop only exact normalized text with temporal cross-source overlap."""

    def __init__(self, minimum_overlap_seconds: float = 0.05) -> None:
        if minimum_overlap_seconds < 0:
            raise ValueError("minimum_overlap_seconds must be non-negative")
        self.minimum_overlap_seconds = minimum_overlap_seconds
        self._accepted: dict[str, _Candidate] = {}
        self._ambiguous: set[str] = set()
        self._duplicate_count = 0

    def register(self, segment_id: str, source: str, segment: Any) -> DuplicateDecision:
        """Return a decision and retain unique candidates for future matches."""
        if not segment_id:
            raise ValueError("segment_id must not be empty")
        if not source:
            raise ValueError("source must not be empty")
        normalized = normalize_text(segment.text)
        for candidate in self._accepted.values():
            if candidate.source == source:
                continue
            overlap = _overlap(segment, candidate.segment)
            if overlap <= self.minimum_overlap_seconds:
                continue
            if normalized and normalized == normalize_text(candidate.segment.text):
                self._ambiguous.add(candidate.segment_id)
                self._duplicate_count += 1
                return DuplicateDecision(
                    duplicate=True,
                    canonical_id=candidate.segment_id,
                    source=source,
                    overlap_seconds=overlap,
                    ambiguous=True,
                    reason="exact normalized text with cross-source overlap",
                )
        self._accepted[segment_id] = _Candidate(segment_id, source, segment)
        return DuplicateDecision(False, segment_id, source)

    def is_ambiguous(self, segment_id: str) -> bool:
        return segment_id in self._ambiguous

    @property
    def duplicate_count(self) -> int:
        return self._duplicate_count

    def reset(self) -> None:
        self._accepted.clear()
        self._ambiguous.clear()
        self._duplicate_count = 0


def normalize_text(text: str) -> str:
    """Normalize punctuation, case and whitespace without fuzzy matching."""
    normalized = unicodedata.normalize("NFKC", str(text)).casefold()
    punctuation = string.punctuation + "…—–«»„“”"
    normalized = normalized.translate(str.maketrans("", "", punctuation))
    return re.sub(r"\s+", " ", normalized).strip()


def _overlap(first: Any, second: Any) -> float:
    return min(float(first.end), float(second.end)) - max(
        float(first.start), float(second.start)
    )
