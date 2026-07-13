"""
Whispered – YouTube description formatter
Converts a chapter list into a YouTube-ready timecode block.
"""

from __future__ import annotations

from core.logger import get_logger

logger = get_logger(__name__)

# YouTube silently disables chapters altogether if any two consecutive
# timestamps are closer together than this.
_MIN_CHAPTER_GAP_SECONDS = 10


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

    Input: list of {"start": int/float/str, "title": str}
    Rules:
    - Skip items with blank title.
    - Coerce start to int; skip on failure.
    - Sort ascending by start.
    - Drop items whose start <= previous kept start (deduplicate/invert).
    - Drop items closer than 10s to the previously *kept* item — YouTube
      silently disables chapters entirely if any gap is smaller than that.
    - Force first kept item's start to 0 (YouTube requirement).
    - Join with newlines; return "" if nothing valid.
    """
    valid = []
    for item in chapters:
        title = item.get("title", "")
        if not isinstance(title, str) or not title.strip():
            continue
        try:
            start = int(item.get("start", 0))
        except (TypeError, ValueError):
            continue
        valid.append((start, title.strip()))

    if not valid:
        return ""

    valid.sort(key=lambda x: x[0])

    kept: list[tuple[int, str]] = []
    for start, title in valid:
        if not kept or start > kept[-1][0]:
            kept.append((start, title))

    if not kept:
        return ""

    spaced: list[tuple[int, str]] = [kept[0]]
    for start, title in kept[1:]:
        if start - spaced[-1][0] >= _MIN_CHAPTER_GAP_SECONDS:
            spaced.append((start, title))
    kept = spaced

    if len(kept) < 3:
        logger.warning(
            "Only %d chapter(s) survive the %ds minimum-gap filter; "
            "YouTube requires at least 3 to display chapters.",
            len(kept), _MIN_CHAPTER_GAP_SECONDS,
        )

    # YouTube requires first chapter at 0:00
    kept[0] = (0, kept[0][1])

    return "\n".join(
        f"{format_youtube_timestamp(start)} {title}" for start, title in kept
    )
