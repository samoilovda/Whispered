"""
Whispered – YouTube description formatter
Converts a chapter list into a YouTube-ready timecode block.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.logger import get_logger

logger = get_logger(__name__)

# YouTube silently disables chapters altogether if any two consecutive
# timestamps are closer together than this.
_MIN_CHAPTER_GAP_SECONDS = 10

# YouTube only shows chapters when the description lists at least this many.
MIN_YOUTUBE_CHAPTERS = 3

# prompts/chapters.md asks the model never to leave more than this between
# two chapter starts. A longer chapter is still valid for YouTube — it is a
# navigation hint for the user, not a reason to drop anything.
LONG_CHAPTER_SECONDS = 450

# ChapterIssue.kind values. The first three mean the chapter was left out of
# the timecode block; the rest describe a chapter that was kept.
ISSUE_INVALID = "invalid"          # blank title or non-numeric start
ISSUE_DUPLICATE = "duplicate"      # start not after the previous kept chapter
ISSUE_TOO_CLOSE = "too_close"      # less than 10 s after the previous kept one
ISSUE_FIRST_MOVED = "first_moved"  # first kept chapter moved to 0:00
ISSUE_LONG = "long"                # longer than LONG_CHAPTER_SECONDS
ISSUE_PAST_END = "past_end"        # starts after the end of the recording

DROPPED_ISSUES = frozenset({ISSUE_INVALID, ISSUE_DUPLICATE, ISSUE_TOO_CLOSE})


@dataclass(frozen=True)
class ChapterIssue:
    """One thing the timecode block changed about, or warns about, a chapter.

    *start* is the chapter's own start in seconds (``None`` when it could not
    be parsed). *seconds* depends on *kind*: the gap to the previous kept
    chapter for ``too_close``, the chapter's length for ``long``, and the
    original start for ``first_moved``.
    """
    kind: str
    start: int | None
    title: str
    seconds: int = 0


@dataclass(frozen=True)
class ChapterCheck:
    """Result of check_chapters(): the ``(start, title)`` pairs that end up
    in the YouTube timecode block, plus everything that was changed on the
    way so the UI can say so instead of only logging it."""
    chapters: tuple[tuple[int, str], ...]
    issues: tuple[ChapterIssue, ...]

    @property
    def shows_on_youtube(self) -> bool:
        return len(self.chapters) >= MIN_YOUTUBE_CHAPTERS

    def issues_of(self, *kinds: str) -> tuple[ChapterIssue, ...]:
        return tuple(issue for issue in self.issues if issue.kind in kinds)


def check_chapters(chapters: list[dict], duration: float | None = None) -> ChapterCheck:
    """Apply YouTube's chapter rules to a model-generated chapter list.

    Input: list of {"start": int/float/str, "title": str}
    Rules (format_youtube_description renders exactly ``.chapters``):
    - Skip items with blank title or a start that is not a number.
    - Sort ascending by start.
    - Drop items whose start <= previous kept start (deduplicate/invert).
    - Drop items closer than 10s to the previously *kept* item — YouTube
      silently disables chapters entirely if any gap is smaller than that.
    - Force first kept item's start to 0 (YouTube requirement).

    Kept chapters are additionally flagged when longer than
    LONG_CHAPTER_SECONDS, or — given the recording *duration* — when they
    start at or after its end.
    """
    issues: list[ChapterIssue] = []
    valid: list[tuple[int, str]] = []
    for item in chapters:
        raw_title = item.get("title", "")
        title = raw_title.strip() if isinstance(raw_title, str) else ""
        try:
            start: int | None = int(item.get("start", 0))
        except (TypeError, ValueError):
            start = None
        if start is None or not title:
            issues.append(ChapterIssue(ISSUE_INVALID, start, title))
            continue
        valid.append((start, title))

    valid.sort(key=lambda x: x[0])

    kept: list[tuple[int, str]] = []
    for start, title in valid:
        if kept and start <= kept[-1][0]:
            issues.append(ChapterIssue(ISSUE_DUPLICATE, start, title))
        elif kept and start - kept[-1][0] < _MIN_CHAPTER_GAP_SECONDS:
            issues.append(ChapterIssue(ISSUE_TOO_CLOSE, start, title, start - kept[-1][0]))
        else:
            kept.append((start, title))

    if kept and kept[0][0] != 0:
        issues.append(ChapterIssue(ISSUE_FIRST_MOVED, 0, kept[0][1], kept[0][0]))
        kept[0] = (0, kept[0][1])

    end = int(duration) if duration and duration > 0 else None
    for i, (start, title) in enumerate(kept):
        next_start = kept[i + 1][0] if i + 1 < len(kept) else end
        if next_start is not None and next_start - start > LONG_CHAPTER_SECONDS:
            issues.append(ChapterIssue(ISSUE_LONG, start, title, next_start - start))
        if end is not None and start >= end:
            issues.append(ChapterIssue(ISSUE_PAST_END, start, title))

    return ChapterCheck(chapters=tuple(kept), issues=tuple(issues))


def format_youtube_timestamp(seconds: int) -> str:
    """Format seconds as a YouTube-compatible timestamp.

    Under one hour: M:SS (no leading zero on minutes).
    One hour or more: H:MM:SS.
    """
    seconds = int(seconds)
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}:{s:02d}"
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    return f"{h}:{m:02d}:{s:02d}"


def format_youtube_description(chapters: list[dict]) -> str:
    """Convert a chapter list to a YouTube timecode block.

    Applies check_chapters()'s rules and joins the surviving chapters with
    newlines; returns "" if nothing valid. Use check_chapters() directly to
    find out what was dropped or changed and why.
    """
    kept = check_chapters(chapters).chapters
    if not kept:
        return ""

    if len(kept) < MIN_YOUTUBE_CHAPTERS:
        logger.warning(
            "Only %d chapter(s) survive the %ds minimum-gap filter; "
            "YouTube requires at least %d to display chapters.",
            len(kept), _MIN_CHAPTER_GAP_SECONDS, MIN_YOUTUBE_CHAPTERS,
        )

    return "\n".join(
        f"{format_youtube_timestamp(start)} {title}" for start, title in kept
    )


def compose_full_description(
    description: str | None,
    chapters: list[dict] | None,
    timecodes_label: str = "Timecodes:",
) -> str | None:
    """Fold chapter timecodes into a description so it reads as one
    ready-to-paste YouTube description: hook + summary + label + chapter
    list.

    Returns *description* unchanged if there's no description, no chapters,
    or the chapters don't produce any valid timecode lines (e.g. all were
    filtered out by the minimum-gap rule). *timecodes_label* is caller-
    supplied so this stays free of any i18n dependency — pass a localized
    string (e.g. ``tr("youtube_timecodes_label")``) from the UI layer.
    """
    if not description or not chapters:
        return description
    timecodes = format_youtube_description(chapters)
    if not timecodes:
        return description
    return f"{description}\n\n{timecodes_label}\n{timecodes}"
