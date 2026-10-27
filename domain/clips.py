"""Short clips cut from a recording (N07 pilot).

A clip is a range on one timeline plus a title, chosen by the user and
checked before anything is rendered. Two timelines are kept apart so they
cannot be confused:

* **source** — times in the original recording (what a transcript shows);
* **edited** — times in the recording *after* the Cut tab's removals (what a
  viewer of the edited draft would see). :func:`edited_range_to_source`
  turns a range on the edited timeline into the source pieces it consists
  of — a clip over a removed stretch is several pieces, joined on export.

Subtitles for a clip are the track's cues cut to the clip and shifted to
start at zero. Pure and Qt-free; nothing here runs ffmpeg or changes the
source.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Optional, Sequence

from domain.subtitles import SubtitleCue

CLIPS_SCHEMA_VERSION = 1
SOURCE = "source"
EDITED = "edited"

MIN_CLIP = 1.0
MAX_CLIP = 600.0       # a "short" clip: ten minutes at most (editorial, adjustable by the caller)


@dataclass(frozen=True)
class ClipDraft:
    title: str
    start: float
    end: float
    timeline: str = SOURCE      # which timeline start/end are on
    # The transcript this clip was chosen against: editing the list must not
    # make an old clip look current. Not part of the clip's identity.
    transcript_revision: str = field(default="", compare=False)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {"title": self.title, "start": self.start, "end": self.end, "timeline": self.timeline,
                "transcript_revision": self.transcript_revision}

    @classmethod
    def from_dict(cls, data: Any) -> Optional["ClipDraft"]:
        try:
            timeline = EDITED if data.get("timeline") == EDITED else SOURCE
            return cls(str(data.get("title", "")), float(data["start"]), float(data["end"]), timeline,
                       str(data.get("transcript_revision", "")))
        except (AttributeError, KeyError, TypeError, ValueError):
            return None


@dataclass
class ClipSet:
    """The clips of one record and the transcript revision they were
    chosen against."""

    clips: list[ClipDraft] = field(default_factory=list)
    transcript_revision: str = ""

    def to_dict(self) -> dict:
        return {"schema_version": CLIPS_SCHEMA_VERSION, "transcript_revision": self.transcript_revision,
                "clips": [c.to_dict() for c in self.clips]}

    @classmethod
    def from_dict(cls, data: Any) -> Optional["ClipSet"]:
        """``None`` for a newer schema (left on disk, shown as unsupported)."""
        if not isinstance(data, dict):
            return cls()
        if int(data.get("schema_version", 1)) > CLIPS_SCHEMA_VERSION:
            return None
        clips = [c for c in (ClipDraft.from_dict(e) for e in data.get("clips", [])) if c is not None]
        revision = str(data.get("transcript_revision", ""))
        # A file from before clips carried their own revision: they all
        # were chosen against the set's.
        clips = [replace(c, transcript_revision=revision) if not c.transcript_revision else c
                 for c in clips]
        return cls(clips, revision)


# ------------------------------------------------------------------ checks

CLIP_EMPTY_TITLE = "empty_title"
CLIP_BAD_RANGE = "bad_range"
CLIP_TOO_SHORT = "too_short"
CLIP_TOO_LONG = "too_long"
CLIP_BEYOND_END = "beyond_end"
CLIP_NO_PIECES = "no_pieces"


def check_clip(
    clip: ClipDraft, *, media_duration: Optional[float] = None,
    edited_duration: Optional[float] = None, max_length: float = MAX_CLIP,
) -> list[str]:
    """Problems that stop a clip being rendered (empty list = fine)."""
    problems = []
    if not clip.title.strip():
        problems.append(CLIP_EMPTY_TITLE)
    if clip.start < 0 or clip.end <= clip.start:
        problems.append(CLIP_BAD_RANGE)
        return problems
    if clip.duration < MIN_CLIP:
        problems.append(CLIP_TOO_SHORT)
    if clip.duration > max_length:
        problems.append(CLIP_TOO_LONG)
    limit = edited_duration if clip.timeline == EDITED else media_duration
    if limit is not None and clip.end > limit + 0.01:
        problems.append(CLIP_BEYOND_END)
    return problems


# ------------------------------------------------------------------ timelines

def edited_range_to_source(
    kept: Sequence[Any], start: float, end: float,
) -> list[tuple[float, float]]:
    """The source ``(start, end)`` pieces behind the range ``start..end`` of
    the edited timeline, in order. *kept* are the segments left after the
    cut (objects with ``.start``/``.end`` in source seconds, in order)."""
    pieces: list[tuple[float, float]] = []
    cursor = 0.0           # where the next kept segment begins on the edited timeline
    for seg in kept:
        length = float(seg.end) - float(seg.start)
        if length <= 0:
            continue
        seg_from, seg_to = cursor, cursor + length
        lo, hi = max(start, seg_from), min(end, seg_to)
        if hi > lo:
            pieces.append((round(float(seg.start) + (lo - seg_from), 3),
                           round(float(seg.start) + (hi - seg_from), 3)))
        cursor = seg_to
    return pieces


def source_to_edited(kept: Sequence[Any], t: float) -> Optional[float]:
    """Where source time *t* lies on the edited timeline, or ``None`` when
    it falls inside a removed stretch."""
    cursor = 0.0
    for seg in kept:
        length = float(seg.end) - float(seg.start)
        if length <= 0:
            continue
        if float(seg.start) <= t <= float(seg.end):
            return round(cursor + (t - float(seg.start)), 3)
        cursor += length
    return None


def edited_duration(kept: Sequence[Any]) -> float:
    return sum(max(0.0, float(s.end) - float(s.start)) for s in kept)


def source_pieces(clip: ClipDraft, kept: Sequence[Any]) -> list[tuple[float, float]]:
    """The source ranges that make up *clip* — one for a source-timeline
    clip, possibly several for an edited-timeline one."""
    if clip.timeline == SOURCE:
        return [(clip.start, clip.end)]
    return edited_range_to_source(kept, clip.start, clip.end)


# ------------------------------------------------------------------ subtitles

def cues_for_clip(
    cues: Sequence[SubtitleCue], pieces: Sequence[tuple[float, float]],
) -> list[SubtitleCue]:
    """*cues* cut to the clip's source *pieces* and moved onto the clip's own
    timeline (it starts at 0, pieces follow each other). A cue that straddles
    a boundary is shortened to what is inside; one entirely outside is
    dropped."""
    out: list[SubtitleCue] = []
    offset = 0.0
    for piece_start, piece_end in pieces:
        for cue in cues:
            lo, hi = max(cue.start, piece_start), min(cue.end, piece_end)
            if hi - lo >= 0.05:
                out.append(SubtitleCue(round(offset + lo - piece_start, 3),
                                       round(offset + hi - piece_start, 3), cue.text))
        offset += piece_end - piece_start
    return out


def safe_file_stem(title: str) -> str:
    """A file name stem from a title: letters, digits, dashes."""
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in title).strip(" ._")
    return (cleaned or "clip")[:80]
