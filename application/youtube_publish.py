"""Assembling and validating a YouTube publish package.

Qt-free. Limits follow YouTube's documented ones: title <= 100 characters,
description <= 5000 bytes (UTF-8), tags <= 500 characters in total, no
angle brackets in title/description.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from application.user_edits import is_stale, set_edit
from domain.latest_source import find_companion_video
from domain.youtube_publish import PublishIssue, PublishPackage
from utils import SUPPORTED_FORMATS

TITLE_MAX_CHARS = 100
DESCRIPTION_MAX_BYTES = 5000
TAGS_MAX_CHARS = 500
THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024

_VIDEO_SUFFIXES = frozenset({".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv", ".m4v"})
_LEADING_NUMBER = re.compile(r"^\s*\d+\s*[.)]\s+")
_QUOTE_PAIRS = (('"', '"'), ("«", "»"), ("“", "”"), ("'", "'"))


def _as_list(raw: "Iterable[object] | str | None") -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(item) for item in raw]


def _strip_quotes(text: str) -> str:
    for left, right in _QUOTE_PAIRS:
        if len(text) >= 2 and text.startswith(left) and text.endswith(right):
            return text[1:-1].strip()
    return text


def normalize_titles(raw: "Iterable[object] | str | None") -> list[str]:
    """Clean generated title candidates: drop list numbering, wrapping
    quotes, blank entries and case-insensitive duplicates."""
    titles: list[str] = []
    seen: set[str] = set()
    for chunk in _as_list(raw):
        for line in chunk.splitlines():
            title = _strip_quotes(_LEADING_NUMBER.sub("", line).strip())
            key = title.casefold()
            if title and key not in seen:
                seen.add(key)
                titles.append(title)
    return titles


def parse_tags(raw: "Iterable[object] | str | None") -> list[str]:
    """Split on commas/newlines, drop ``#``, trim, de-duplicate ignoring case."""
    tags: list[str] = []
    seen: set[str] = set()
    for chunk in _as_list(raw):
        for part in re.split(r"[,\n]", chunk):
            tag = part.strip().lstrip("#").strip()
            key = tag.casefold()
            if tag and key not in seen:
                seen.add(key)
                tags.append(tag)
    return tags


def tags_length(tags: Iterable[str]) -> int:
    """YouTube's tag budget: characters, with a tag containing a space
    counted as quoted (+2); separators between tags count as one each."""
    items = list(tags)
    total = sum(len(tag) + (2 if " " in tag else 0) for tag in items)
    return total + max(len(items) - 1, 0)


def fit_tags(tags: Iterable[str]) -> tuple[list[str], list[str]]:
    """Return ``(kept, dropped)``: tags are kept in order until the 500
    character budget is exhausted; the rest are dropped from the end."""
    kept: list[str] = []
    dropped: list[str] = []
    for tag in tags:
        if not dropped and tags_length([*kept, tag]) <= TAGS_MAX_CHARS:
            kept.append(tag)
        else:
            dropped.append(tag)
    return kept, dropped


def description_bytes(description: str) -> int:
    return len(description.encode("utf-8"))


def build_package(
    *,
    video_path: "str | Path",
    titles: "Iterable[object] | str | None",
    title_index: int = 0,
    description: str,
    tags: "Iterable[object] | str | None",
    cover_path: "str | Path | None" = None,
    language: Optional[str] = None,
    privacy: str = "private",
) -> PublishPackage:
    """Build a package from raw (possibly user-edited) generator output.
    ``title_index`` picks among the normalized titles; out of range falls
    back to the first (or an empty title when there are none)."""
    candidates = normalize_titles(titles)
    if 0 <= title_index < len(candidates):
        title = candidates[title_index]
    else:
        title = candidates[0] if candidates else ""
    return PublishPackage(
        video_path=Path(video_path),
        title=title,
        description=description,
        tags=tuple(parse_tags(tags)),
        thumbnail_path=Path(cover_path) if cover_path else None,
        privacy=privacy,
        language=language,
    )


def validate_package(pkg: PublishPackage) -> list[PublishIssue]:
    issues: list[PublishIssue] = []

    if not pkg.video_path.is_file():
        issues.append(PublishIssue("video_missing", "yt_publish_issue_video_missing"))
    elif pkg.video_path.suffix.lower() not in _VIDEO_SUFFIXES:
        issues.append(PublishIssue("source_not_video", "yt_publish_issue_source_not_video"))

    if not pkg.title.strip():
        issues.append(PublishIssue("title_empty", "yt_publish_issue_title_empty"))
    elif len(pkg.title) > TITLE_MAX_CHARS:
        issues.append(PublishIssue(
            "title_too_long", "yt_publish_issue_title_too_long",
            (("count", len(pkg.title)), ("limit", TITLE_MAX_CHARS)),
        ))
    if "<" in pkg.title or ">" in pkg.title:
        issues.append(PublishIssue("title_brackets", "yt_publish_issue_title_brackets"))

    size = description_bytes(pkg.description)
    if size > DESCRIPTION_MAX_BYTES:
        issues.append(PublishIssue(
            "description_too_long", "yt_publish_issue_description_too_long",
            (("count", size), ("limit", DESCRIPTION_MAX_BYTES)),
        ))
    if "<" in pkg.description or ">" in pkg.description:
        issues.append(PublishIssue("description_brackets", "yt_publish_issue_description_brackets"))

    _, dropped = fit_tags(pkg.tags)
    if dropped:
        issues.append(PublishIssue(
            "tags_trimmed", "yt_publish_issue_tags_trimmed",
            (("count", len(dropped)), ("limit", TAGS_MAX_CHARS)), blocking=False,
        ))

    if pkg.thumbnail_path is not None:
        if not pkg.thumbnail_path.is_file():
            issues.append(PublishIssue(
                "thumbnail_missing", "yt_publish_issue_thumbnail_missing", blocking=False,
            ))
        elif pkg.thumbnail_path.stat().st_size > THUMBNAIL_MAX_BYTES:
            issues.append(PublishIssue(
                "thumbnail_too_large", "yt_publish_issue_thumbnail_too_large", blocking=False,
            ))
    return issues


def find_video_source(source_path: "str | Path | None") -> Optional[Path]:
    """The video to publish for a recipe's source: the source itself when
    it is a video that still exists, else the video recorded with an
    audio source (see ``domain.latest_source.find_companion_video``)."""
    if not source_path:
        return None
    path = Path(source_path)
    if path.suffix.lower() in _VIDEO_SUFFIXES and path.suffix.lower() in SUPPORTED_FORMATS and path.is_file():
        return path
    if path.is_file():
        return find_companion_video(path, _VIDEO_SUFFIXES & SUPPORTED_FORMATS)
    return None


# ── Wizard draft ───────────────────────────────────────────────────────
#
# What the publish wizard agreed on (title, description, tags, the
# speakers' names, the cover's own line) is kept per record in
# ``<artifact_dir>/youtube_publish.draft.json`` so closing the dialog does
# not lose it. Only fields that differ from what the wizard would show
# without a draft are stored, each with the fingerprint of that default
# (``application.user_edits`` — the same overlay format the YouTube tab
# uses). Rule for a regenerated package (or a later edit in the YouTube
# tab): a field whose default has changed since the draft was written is
# stale — the newer text wins and the wizard says so; fields that are not
# derived from the package (guest, cover text) stay as they were.

DRAFT_FILE = "youtube_publish.draft.json"
DRAFT_FIELDS = ("title", "description", "tags", "host", "guest", "cover_text")


def draft_defaults(texts: dict, host: str = "") -> dict[str, str]:
    """The wizard's fields as filled from ``YouTubePanel.publish_texts()``
    and the remembered host, with no draft."""
    titles = normalize_titles(texts.get("titles"))
    return {
        "title": titles[0] if titles else "",
        "description": str(texts.get("description") or ""),
        "tags": ", ".join(parse_tags(texts.get("tags"))),
        "host": host,
        "guest": "",
        "cover_text": "",
    }


def restore_draft(
    draft: dict, defaults: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    """``(fields, outdated)``: *defaults* with the draft's still-valid edits
    applied, and the names of edited fields dropped because their default
    has changed since (see the rule above)."""
    fields = dict(defaults)
    outdated: list[str] = []
    for key in DRAFT_FIELDS:
        value = draft.get(key)
        if not isinstance(value, str):
            continue
        if is_stale(draft, key, defaults.get(key, "")):
            # Nothing lost when the new default is what the user had typed
            # (e.g. the host remembered in Config on approving the cover).
            if value != defaults.get(key, ""):
                outdated.append(key)
            continue
        fields[key] = value
    return fields, outdated


def record_draft(fields: dict[str, str], defaults: dict[str, str]) -> dict:
    """The draft to store for *fields*: only what differs from *defaults*
    (an empty dict when nothing does)."""
    draft: dict = {}
    for key in DRAFT_FIELDS:
        draft = set_edit(draft, key, fields.get(key, ""), defaults.get(key, ""))
    return draft
