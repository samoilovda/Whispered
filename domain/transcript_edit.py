"""The transcript editor's text buffer, as data (UI/UX audit 2026-10-18,
UX-05 / T05).

The editor shows one line per segment, ``[HH:MM:SS.mmm] [speaker] text``.
``parse_edit_buffer()`` turns that buffer back into segments *without*
touching the open ``TranscriptionResult``: it either returns the new
segment list or says exactly which line is wrong. The caller applies the
segments only once it has decided to commit them, so an invalid buffer is
never half-applied and never "fixed" silently (a malformed timestamp used
to be glued onto the previous segment as plain text).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from domain.transcription import Segment

EDIT_LINE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2}\.\d{3})\](?:\s*\[([^\]]*)\])?\s*(.*)")
# A line that *looks* like it starts with a timestamp but doesn't parse as
# one: "[00:01:2.000]", "[0:01:02.000] …" — reported, not read as text.
_TIMESTAMP_LIKE_RE = re.compile(r"^\[\d[\d:.,]*\]")

# Error codes (the UI maps them to localized messages).
ERROR_EMPTY = "empty"
ERROR_MALFORMED_TIMESTAMP = "malformed_timestamp"
ERROR_OUT_OF_ORDER = "out_of_order"
ERROR_TEXT_BEFORE_FIRST = "text_before_first"


def format_edit_timestamp(seconds: float) -> str:
    """``HH:MM:SS.mmm`` — the editor's timestamp format."""
    millis = int(round(max(0.0, seconds) * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def parse_edit_timestamp(text: str) -> Optional[float]:
    """Seconds for ``HH:MM:SS.mmm``, or ``None`` when it isn't one."""
    try:
        hours, minutes, rest = text.split(":")
        secs, ms = rest.split(".")
        if int(minutes) >= 60 or int(secs) >= 60:
            return None
        return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int(ms) / 1000.0
    except ValueError:
        return None


def render_edit_buffer(segments: Sequence[Segment]) -> str:
    """The editor text for *segments*: one line per segment."""
    lines = []
    for seg in segments:
        stamp = format_edit_timestamp(seg.start)
        if seg.speaker:
            lines.append(f"[{stamp}] [{seg.speaker}] {seg.text.strip()}")
        else:
            lines.append(f"[{stamp}] {seg.text.strip()}")
    return "\n".join(lines)


@dataclass(frozen=True)
class EditParse:
    """``segments`` when the buffer is valid; otherwise ``error`` (one of
    the ``ERROR_*`` codes) and the 1-based ``line`` it was found on."""

    segments: list[Segment] = field(default_factory=list)
    error: str = ""
    line: int = 0

    @property
    def ok(self) -> bool:
        return not self.error


def parse_edit_buffer(
    text: str, original: Sequence[Segment], duration: float,
) -> EditParse:
    """Parse the editor *text* into a fresh segment list.

    Rebuilding (rather than mutating in place) keeps the result consistent
    when lines are added, removed or split. A line whose timestamp and
    speaker still match the segment at its position keeps that segment's
    exact timing and words (a pure text edit must not lose the
    transcriber's timing); its confidence survives only if its text is
    unchanged — a corrected segment is no longer "uncertain".
    """
    parsed: list[tuple[float, Optional[str], list[str]]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        match = EDIT_LINE_RE.match(line)
        if match:
            start = parse_edit_timestamp(match.group(1))
            if start is None:
                return EditParse(error=ERROR_MALFORMED_TIMESTAMP, line=number)
            if parsed and start < parsed[-1][0]:
                return EditParse(error=ERROR_OUT_OF_ORDER, line=number)
            parsed.append((start, match.group(2) or None, [match.group(3)]))
        elif _TIMESTAMP_LIKE_RE.match(line):
            return EditParse(error=ERROR_MALFORMED_TIMESTAMP, line=number)
        elif parsed:
            parsed[-1][2].append(line)  # continuation of the previous segment
        else:
            return EditParse(error=ERROR_TEXT_BEFORE_FIRST, line=number)
    if not parsed:
        return EditParse(error=ERROR_EMPTY, line=1)

    segments: list[Segment] = []
    for i, (start, speaker, parts) in enumerate(parsed):
        body = " ".join(parts).strip()
        previous = original[i] if i < len(original) else None
        # The buffer shows milliseconds; a start of 12.3456 reads back as
        # 12.346 and still is the same segment.
        same_start = previous is not None and abs(previous.start - start) < 0.0015
        if previous is not None and same_start and previous.speaker == speaker:
            segments.append(Segment(
                start=previous.start,
                end=previous.end,
                text=body,
                speaker=previous.speaker,
                words=list(previous.words),
                confidence=previous.confidence if body == previous.text.strip() else None,
            ))
            continue
        if i + 1 < len(parsed):
            end = max(parsed[i + 1][0], start)
        elif i < len(original):
            end = max(original[i].end, start)
        else:
            end = max(duration, start)
        segments.append(Segment(start=start, end=end, text=body, speaker=speaker))
    return EditParse(segments=segments)


def same_segments(a: Sequence[Segment], b: Sequence[Segment]) -> bool:
    """Whether committing *b* over *a* would change anything a user sees
    or a revision records (no-op edits must not create versions)."""
    if len(a) != len(b):
        return False
    return all(
        x.start == y.start and x.end == y.end and x.text.strip() == y.text.strip()
        and x.speaker == y.speaker
        for x, y in zip(a, b)
    )
