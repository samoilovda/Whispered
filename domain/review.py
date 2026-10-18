"""Review marks for the proofreading mode (N01).

A passage needs a look when the user flagged it, or when the recogniser was
unsure of it (``Segment.confidence`` known and below ``LOW_CONFIDENCE``) and
nobody has reviewed it yet. Confidence is a hint, not a verdict: an unknown
confidence is never treated as bad, and marking a passage reviewed never
rewrites its original confidence.

Marks are anchored like a cut (domain/cut_plan.py): time range, speaker and
a fingerprint of the text. A *reviewed* mark holds only while both the times
and the text are unchanged — editing a passage puts it back in the queue to
be checked again. A *flag* is the user's own note and follows the passage's
time range through text corrections.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from domain.cut_plan import CutAnchor, text_fingerprint
from domain.transcription import LOW_CONFIDENCE

REVIEW_SCHEMA_VERSION = 1
REVIEWED = "reviewed"
FLAGGED = "flagged"


@dataclass(frozen=True)
class ReviewMark:
    anchor: CutAnchor
    state: str            # REVIEWED | FLAGGED

    def to_dict(self) -> dict:
        return {**self.anchor.to_dict(), "state": self.state}

    @classmethod
    def from_dict(cls, data: dict) -> "ReviewMark":
        return cls(CutAnchor.from_dict(data), str(data.get("state", REVIEWED)))


def mark_state(segment: Any, marks: Sequence[ReviewMark]) -> Optional[str]:
    """The mark that still holds for *segment*, or ``None``."""
    fingerprint = text_fingerprint(segment.text or "")
    for mark in marks:
        if not mark.anchor.same_time(segment):
            continue
        if mark.state == FLAGGED:
            return FLAGGED
        if mark.anchor.text_fp == fingerprint:
            return REVIEWED
    return None


def needs_review(segment: Any, marks: Sequence[ReviewMark]) -> bool:
    state = mark_state(segment, marks)
    if state == FLAGGED:
        return True
    if state == REVIEWED:
        return False
    confidence = getattr(segment, "confidence", None)
    return confidence is not None and confidence < LOW_CONFIDENCE


def review_indices(segments: Sequence[Any], marks: Sequence[ReviewMark]) -> list[int]:
    return [i for i, seg in enumerate(segments) if needs_review(seg, marks)]


def with_mark(
    marks: Sequence[ReviewMark], segment: Any, state: Optional[str],
) -> tuple[ReviewMark, ...]:
    """*marks* with *segment*'s mark set to *state* (``None`` removes it)."""
    kept = [m for m in marks if not m.anchor.same_time(segment)]
    if state is not None:
        kept.append(ReviewMark(CutAnchor.of(segment), state))
    return tuple(kept)


def holding(segments: Sequence[Any], marks: Sequence[ReviewMark]) -> tuple[ReviewMark, ...]:
    """Only the marks that still hold on *segments* (what is worth storing)."""
    return tuple(
        m for m in marks
        if any(mark_state(seg, [m]) == m.state for seg in segments)
    )


def counts(segments: Sequence[Any], marks: Sequence[ReviewMark]) -> tuple[int, int]:
    """(passages that were ever in the review set, still to review): those
    flagged or unsure, and how many of them nobody has checked."""
    total = remaining = 0
    for seg in segments:
        state = mark_state(seg, marks)
        confidence = getattr(seg, "confidence", None)
        unsure = confidence is not None and confidence < LOW_CONFIDENCE
        if state == FLAGGED or state == REVIEWED or unsure:
            total += 1
            if state == FLAGGED or (state is None and unsure):
                remaining += 1
    return total, remaining


def to_dict(marks: Sequence[ReviewMark], revision: str = "") -> dict:
    return {"schema_version": REVIEW_SCHEMA_VERSION, "revision": revision,
            "marks": [m.to_dict() for m in marks]}


def from_dict(data: dict) -> Optional[tuple[ReviewMark, ...]]:
    """``None`` for a newer schema (kept as stored, shown as unsupported)."""
    if int(data.get("schema_version", 1)) > REVIEW_SCHEMA_VERSION:
        return None
    return tuple(ReviewMark.from_dict(m) for m in data.get("marks", []))
