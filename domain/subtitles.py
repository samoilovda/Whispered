"""Subtitle cues as an editable layer of their own (N02).

Cues start as a re-cut of the transcript's segments to reading limits —
lines per cue, characters per line, characters per second — and are then
edited, split and merged *without ever changing the transcript*. The limits
(a "preset") are editorial recommendations, configurable, not a standard
for every language. Timing uses segment times only: word-level times stay
off by default (they break Cyrillic transcripts), so a long segment's time
is shared among its cues in proportion to their text.

Pure and Qt-free: building, validating, editing and (de)serialising cues.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Sequence

CUES_SCHEMA_VERSION = 1

# Issue codes.
EMPTY = "empty"
BAD_TIME = "bad_time"            # end not after start
OVERLAP = "overlap"
TOO_SHORT = "too_short"
TOO_LONG = "too_long"
LINE_TOO_LONG = "line_too_long"
TOO_MANY_LINES = "too_many_lines"
CPS_HIGH = "cps_high"
BEYOND_END = "beyond_end"
BLOCKING = frozenset({EMPTY, BAD_TIME})   # an export refuses these


@dataclass(frozen=True)
class SubtitlePreset:
    """Editorial limits; the defaults suit two-line cues in Latin and
    Cyrillic text at a comfortable reading speed."""

    max_lines: int = 2
    max_chars_per_line: int = 42
    max_cps: float = 17.0
    min_duration: float = 1.0
    max_duration: float = 7.0

    def to_dict(self) -> dict:
        return {"max_lines": self.max_lines, "max_chars_per_line": self.max_chars_per_line,
                "max_cps": self.max_cps, "min_duration": self.min_duration,
                "max_duration": self.max_duration}

    @classmethod
    def from_dict(cls, data: Any) -> "SubtitlePreset":
        """Valid values only; anything else falls back to the default."""
        base = cls()
        if not isinstance(data, dict):
            return base

        def number(key: str, low: float, high: float, default: float) -> float:
            try:
                value = float(data[key])
            except (KeyError, TypeError, ValueError):
                return default
            return value if low <= value <= high else default

        return cls(
            max_lines=int(number("max_lines", 1, 4, base.max_lines)),
            max_chars_per_line=int(number("max_chars_per_line", 15, 100, base.max_chars_per_line)),
            max_cps=number("max_cps", 5, 40, base.max_cps),
            min_duration=number("min_duration", 0.2, 5, base.min_duration),
            max_duration=number("max_duration", 2, 20, base.max_duration),
        )


@dataclass(frozen=True)
class SubtitleCue:
    start: float
    end: float
    text: str                       # lines separated by "\n"

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def lines(self) -> list[str]:
        return self.text.split("\n")

    @property
    def chars(self) -> int:
        return len(self.text.replace("\n", ""))


@dataclass(frozen=True)
class Issue:
    index: int                      # 0-based cue index
    code: str
    detail: str = ""


def _ms(seconds: float) -> int:
    return int(round(seconds * 1000))


# ------------------------------------------------------------------ building

def wrap_text(text: str, max_chars: int) -> list[str]:
    """Greedy word wrap; a word longer than the line stays whole (flagged by
    :func:`validate`, never cut in the middle)."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= max_chars:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    # Widow control: a last line of a word or two reads badly — pull one
    # word down from the line above when it fits.
    if len(lines) > 1 and len(lines[-1]) < 8:
        head, _, tail = lines[-2].rpartition(" ")
        if head and len(tail) + 1 + len(lines[-1]) <= max_chars:
            lines[-2], lines[-1] = head, f"{tail} {lines[-1]}"
    return lines


def cues_from_segments(segments: Sequence[Any], preset: SubtitlePreset = SubtitlePreset()) -> list[SubtitleCue]:
    """Re-cut *segments* to *preset*'s reading limits."""
    cues: list[SubtitleCue] = []
    for seg in segments:
        lines = wrap_text((seg.text or "").strip(), preset.max_chars_per_line)
        if not lines:
            continue
        groups = [lines[i:i + preset.max_lines] for i in range(0, len(lines), preset.max_lines)]
        weights = [max(1, sum(len(line) for line in group)) for group in groups]
        total = sum(weights)
        start = float(seg.start)
        span = max(0.0, float(seg.end) - start)
        cursor = start
        for n, group in enumerate(groups):
            end = float(seg.end) if n == len(groups) - 1 else cursor + span * weights[n] / total
            cues.append(SubtitleCue(round(cursor, 3), round(end, 3), "\n".join(group)))
            cursor = end
    return cues


# ------------------------------------------------------------------ checking

def validate(
    cues: Sequence[SubtitleCue], preset: SubtitlePreset = SubtitlePreset(),
    media_duration: Optional[float] = None,
) -> list[Issue]:
    """Every problem a reader of the cues would meet, by cue."""
    issues: list[Issue] = []
    previous_end: Optional[float] = None
    for i, cue in enumerate(cues):
        if not cue.text.strip():
            issues.append(Issue(i, EMPTY))
        if cue.end <= cue.start:
            issues.append(Issue(i, BAD_TIME))
        elif cue.start < 0:
            issues.append(Issue(i, BAD_TIME, "negative"))
        if previous_end is not None and cue.start < previous_end - 0.001:
            issues.append(Issue(i, OVERLAP))
        previous_end = max(previous_end or 0.0, cue.end)
        if cue.end > cue.start:
            if cue.duration < preset.min_duration:
                issues.append(Issue(i, TOO_SHORT, f"{cue.duration:.2f}"))
            if cue.duration > preset.max_duration:
                issues.append(Issue(i, TOO_LONG, f"{cue.duration:.2f}"))
            if cue.chars and cue.chars / cue.duration > preset.max_cps:
                issues.append(Issue(i, CPS_HIGH, f"{cue.chars / cue.duration:.1f}"))
        if len(cue.lines) > preset.max_lines:
            issues.append(Issue(i, TOO_MANY_LINES, str(len(cue.lines))))
        longest = max((len(line) for line in cue.lines), default=0)
        if longest > preset.max_chars_per_line:
            issues.append(Issue(i, LINE_TOO_LONG, str(longest)))
        if media_duration is not None and cue.start > media_duration + 0.5:
            issues.append(Issue(i, BEYOND_END))
    return issues


def blocking(issues: Sequence[Issue]) -> list[Issue]:
    return [issue for issue in issues if issue.code in BLOCKING]


# ------------------------------------------------------------------ editing

def split_cue(cues: Sequence[SubtitleCue], index: int, at_char: int) -> list[SubtitleCue]:
    """Split cue *index* after *at_char* characters of its text (moved to
    the nearest space); its time is shared in proportion to the two halves."""
    cue = cues[index]
    flat = cue.text.replace("\n", " ")
    cut = flat.rfind(" ", 0, max(1, at_char) + 1)
    if cut <= 0:
        cut = flat.find(" ", max(1, at_char))
    if cut <= 0 or cut >= len(flat) - 1:
        return list(cues)             # nothing sensible to split
    left, right = flat[:cut].strip(), flat[cut:].strip()
    mid = round(cue.start + cue.duration * len(left) / max(1, len(left) + len(right)), 3)
    return [*cues[:index], SubtitleCue(cue.start, mid, left), SubtitleCue(mid, cue.end, right),
            *cues[index + 1:]]


def merge_cues(cues: Sequence[SubtitleCue], index: int, preset: SubtitlePreset = SubtitlePreset()) -> list[SubtitleCue]:
    """Merge cue *index* with the next one, re-wrapped to the preset."""
    if index < 0 or index + 1 >= len(cues):
        return list(cues)
    a, b = cues[index], cues[index + 1]
    text = "\n".join(wrap_text(a.text.replace("\n", " ") + " " + b.text.replace("\n", " "),
                               preset.max_chars_per_line))
    return [*cues[:index], SubtitleCue(a.start, max(a.end, b.end), text), *cues[index + 2:]]


def with_cue(cues: Sequence[SubtitleCue], index: int, **changes: Any) -> list[SubtitleCue]:
    out = list(cues)
    out[index] = replace(out[index], **changes)
    return out


# ------------------------------------------------------------------ files

def _stamp(seconds: float, comma: bool) -> str:
    ms = max(0, _ms(seconds))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, milli = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{',' if comma else '.'}{milli:03d}"


def _clean_for_file(text: str) -> str:
    """No blank line (it would end the cue) and no arrow (it would read as
    a time line)."""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n".join(line.replace("-->", "–>") for line in lines)


def to_srt(cues: Sequence[SubtitleCue]) -> str:
    blocks = []
    for n, cue in enumerate(cues, start=1):
        blocks.append(f"{n}\n{_stamp(cue.start, True)} --> {_stamp(cue.end, True)}\n"
                      f"{_clean_for_file(cue.text)}\n")
    return "\n".join(blocks)


def to_vtt(cues: Sequence[SubtitleCue]) -> str:
    def esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    blocks = ["WEBVTT\n"]
    for n, cue in enumerate(cues, start=1):
        blocks.append(f"{n}\n{_stamp(cue.start, False)} --> {_stamp(cue.end, False)}\n"
                      f"{esc(_clean_for_file(cue.text))}\n")
    return "\n".join(blocks)


_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})")
_LINE = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)")


def _seconds(stamp: str) -> Optional[float]:
    match = _TIME.fullmatch(stamp.strip())
    if not match:
        return None
    h, m, s, frac = match.groups()
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(frac.ljust(3, "0")) / 1000


def format_time(seconds: float, comma: bool = False) -> str:
    """``HH:MM:SS.mmm`` (``,`` before the milliseconds for SRT)."""
    return _stamp(seconds, comma)


def parse_time(text: str) -> Optional[float]:
    """Seconds from ``HH:MM:SS.mmm`` / ``MM:SS.mmm`` (``,`` or ``.``), else
    ``None`` — what a user may type into a cue's time cell."""
    text = text.strip()
    if text.count(":") == 1:
        text = "0:" + text
    return _seconds(text)


def parse_cues(text: str) -> list[SubtitleCue]:
    """Read SRT or WebVTT back into cues (VTT escapes are undone)."""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = [line for line in block.split("\n")]
        for i, line in enumerate(lines):
            match = _LINE.match(line)
            if match:
                start, end = _seconds(match.group(1)), _seconds(match.group(2))
                if start is None or end is None:
                    break
                body = "\n".join(lines[i + 1:])
                body = body.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
                cues.append(SubtitleCue(start, end, body))
                break
    return cues


# ------------------------------------------------------------------ storage shape

@dataclass
class SubtitleTrack:
    """The stored layer: cues, the preset they were cut with, and the
    transcript revision they were made from."""

    cues: list[SubtitleCue] = field(default_factory=list)
    preset: SubtitlePreset = field(default_factory=SubtitlePreset)
    transcript_revision: str = ""

    def to_dict(self) -> dict:
        return {"schema_version": CUES_SCHEMA_VERSION, "transcript_revision": self.transcript_revision,
                "preset": self.preset.to_dict(),
                "cues": [[c.start, c.end, c.text] for c in self.cues]}

    @classmethod
    def from_dict(cls, data: Any) -> Optional["SubtitleTrack"]:
        """``None`` for a newer schema (left on disk, shown as unsupported)."""
        if not isinstance(data, dict):
            return cls()
        if int(data.get("schema_version", 1)) > CUES_SCHEMA_VERSION:
            return None
        cues = []
        for entry in data.get("cues", []):
            try:
                cues.append(SubtitleCue(float(entry[0]), float(entry[1]), str(entry[2])))
            except (IndexError, TypeError, ValueError):
                continue
        return cls(cues, SubtitlePreset.from_dict(data.get("preset")),
                   str(data.get("transcript_revision", "")))
