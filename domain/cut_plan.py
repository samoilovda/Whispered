"""A record's cut — which stretches of the transcript are left out of the
edit — as data that survives navigation, restarts and transcript edits
(UI/UX audit 2026-10-18, UX-06 / T06).

Bare segment indices are not an identity: a split or merged line shifts
every index after it. A cut is therefore stored as *anchors* — a segment's
time range, speaker and a fingerprint of its text — together with the
transcript revision it was made against. Mapping a plan onto (possibly
edited) segments is deliberately conservative: an anchor moves only to a
segment it identifies unambiguously; anything else is reported as needing
review, and the original decision is kept rather than guessed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

CUT_PLAN_SCHEMA_VERSION = 1

# Times are compared at the editor's millisecond precision.
_TIME_TOLERANCE = 0.0015


def text_fingerprint(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class CutAnchor:
    """One cut segment, identified independently of its index."""

    start: float
    end: float
    speaker: str
    text_fp: str

    @classmethod
    def of(cls, segment: Any) -> "CutAnchor":
        return cls(
            start=float(segment.start), end=float(segment.end),
            speaker=segment.speaker or "", text_fp=text_fingerprint(segment.text or ""),
        )

    def same_time(self, segment: Any) -> bool:
        return (abs(self.start - float(segment.start)) < _TIME_TOLERANCE
                and abs(self.end - float(segment.end)) < _TIME_TOLERANCE
                and self.speaker == (segment.speaker or ""))

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "speaker": self.speaker,
                "text_fp": self.text_fp}

    @classmethod
    def from_dict(cls, data: dict) -> "CutAnchor":
        return cls(float(data["start"]), float(data["end"]),
                   str(data.get("speaker", "")), str(data.get("text_fp", "")))


@dataclass(frozen=True)
class CutPlan:
    """The cut of one record: anchors plus the revision they were made on.
    ``unresolved`` holds anchors a later transcript edit could not place —
    kept so the user's decision isn't lost while they review it."""

    transcript_revision: str = ""
    cuts: tuple[CutAnchor, ...] = ()
    unresolved: tuple[CutAnchor, ...] = ()

    @property
    def needs_review(self) -> bool:
        return bool(self.unresolved)

    def to_dict(self) -> dict:
        return {
            "schema_version": CUT_PLAN_SCHEMA_VERSION,
            "transcript_revision": self.transcript_revision,
            "cuts": [a.to_dict() for a in self.cuts],
            "unresolved": [a.to_dict() for a in self.unresolved],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Optional["CutPlan"]:
        """``None`` for a plan written by a newer schema (kept on disk,
        shown as unsupported, never overwritten by an empty one)."""
        if int(data.get("schema_version", 1)) > CUT_PLAN_SCHEMA_VERSION:
            return None
        return cls(
            transcript_revision=str(data.get("transcript_revision", "")),
            cuts=tuple(CutAnchor.from_dict(a) for a in data.get("cuts", [])),
            unresolved=tuple(CutAnchor.from_dict(a) for a in data.get("unresolved", [])),
        )


def plan_for(segments: Sequence[Any], cut_indices: Iterable[int], revision: str) -> CutPlan:
    """A plan cutting *cut_indices* of *segments*."""
    indices = sorted(i for i in set(cut_indices) if 0 <= i < len(segments))
    return CutPlan(revision, tuple(CutAnchor.of(segments[i]) for i in indices))


@dataclass
class CutResolution:
    indices: set[int] = field(default_factory=set)
    unresolved: list[CutAnchor] = field(default_factory=list)


def resolve(anchors: Iterable[CutAnchor], segments: Sequence[Any]) -> CutResolution:
    """Place *anchors* on *segments*.

    An anchor maps to the one segment with the same time range and speaker
    (a text correction keeps the cut). Failing that, to the one segment
    with the same text fingerprint and speaker (a line that only moved).
    No match, or more than one, leaves it unresolved.
    """
    out = CutResolution()
    for anchor in anchors:
        by_time = [i for i, seg in enumerate(segments) if anchor.same_time(seg)]
        if len(by_time) == 1:
            out.indices.add(by_time[0])
            continue
        if not by_time:
            by_text = [
                i for i, seg in enumerate(segments)
                if (seg.speaker or "") == anchor.speaker
                and text_fingerprint(seg.text or "") == anchor.text_fp
            ]
            if len(by_text) == 1:
                out.indices.add(by_text[0])
                continue
        out.unresolved.append(anchor)
    return out
