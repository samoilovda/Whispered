"""The host's own YouTube titles and descriptions, as style examples.

A small local model imitates an example far better than it follows an
abstract style instruction, so the titles and descriptions the user
approved or rewrote on other records are shown to it next to the
transcript (``core.insights``: "Examples of the host's own style").

Where they come from, most trusted first, per record folder under the
app's output dir:

- ``youtube_upload.queued.json`` — the package approved for upload
  (``application.youtube_autoupload``);
- ``youtube_publish.draft.json`` — the publish wizard's edits
  (``application.youtube_publish``), only the fields the user changed;
- ``youtube_package.user.json`` — the YouTube panel's edit overlay
  (``application.user_edits``): the title picked among the candidates.

Only the most recent few records count, and the current one never does —
its own approved title would be copied rather than imitated.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from application.user_edits import OVERLAY_SUFFIX, fingerprint, load_overlay
from application.youtube_autoupload import QUEUE_FILE
from application.youtube_publish import DRAFT_FILE

# How many other records lend examples, and how many characters each
# insight type's examples may take in the prompt (the block's text, not
# counting its fixed intro).
MAX_RECORDS = 3
MAX_CHARS = 800

_PANEL_OVERLAY = "youtube_package" + OVERLAY_SUFFIX
_RECORD_ID = re.compile(r"-(\d+)$")
# The description's own text ends where the timecodes / footer begin.
_DESCRIPTION_END = re.compile(r"\n\s*(?:\d{1,2}:\d{2}|тайм-?код|timecode|timestamps|chapters|-{3,})",
                              re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class StyleExamples:
    """Titles and description openings from other records, newest first."""

    titles: tuple[str, ...] = ()
    descriptions: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.titles or self.descriptions)

    def for_insights(self) -> dict[str, list[str]]:
        """``{insight type: examples}`` as ``core.insights`` takes them;
        a review pass uses the examples of the type it reviews."""
        mapping: dict[str, list[str]] = {}
        if self.titles:
            mapping["yt_titles"] = list(self.titles)
        if self.descriptions:
            mapping["yt_description"] = list(self.descriptions)
        return mapping

    def fingerprint(self) -> str:
        """Short hash of the examples — part of the YouTube step's prompt
        version, so new examples make the next run regenerate."""
        return fingerprint([list(self.titles), list(self.descriptions)]) if self else ""


def description_text(description: str) -> str:
    """The hook and summary of a published description: what comes before
    its timecodes, footer or hashtags."""
    text = description.replace("\r\n", "\n").strip()
    match = _DESCRIPTION_END.search(text)
    if match:
        text = text[: match.start()]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "\n\n".join(p for p in paragraphs if not p.startswith("#"))


def _cut(text: str, limit: int) -> str:
    """*text* shortened to at most *limit* characters at a sentence end
    (or "" when not even its first sentence fits)."""
    if len(text) <= limit:
        return text
    kept = ""
    for match in _SENTENCE_END.finditer(text):
        if match.start() > limit:
            break
        kept = text[: match.start()]
    return kept.strip()


def _fit(items: list[str], budget: int) -> tuple[str, ...]:
    """*items* within *budget* characters in total: each gets an equal
    share, cut at a sentence end; one that does not fit at all is left out."""
    if not items:
        return ()
    share = budget // len(items)
    fitted = (_cut(item, share) for item in items)
    return tuple(item for item in fitted if item)


def _record_id(folder: Path) -> Optional[str]:
    match = _RECORD_ID.search(folder.name)
    return match.group(1) if match else None


def _record_examples(folder: Path) -> tuple[float, str, str]:
    """``(when, title, description)`` the user approved or edited in
    *folder*; empty strings where there is none."""
    when = 0.0
    title = description = ""
    queued = folder / QUEUE_FILE
    if queued.is_file():
        try:
            data: Any = json.loads(queued.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            title = str(data.get("title") or "").strip()
            description = description_text(str(data.get("description") or ""))
            when = queued.stat().st_mtime
    for name in (DRAFT_FILE, _PANEL_OVERLAY):
        path = folder / name
        overlay = load_overlay(path)
        if not overlay:
            continue
        if not title and isinstance(overlay.get("title"), str):
            title = overlay["title"].strip()
            when = max(when, path.stat().st_mtime)
        if not description and isinstance(overlay.get("description"), str):
            description = description_text(overlay["description"])
            when = max(when, path.stat().st_mtime)
    return when, title, description


def collect_style_examples(
    root: Path,
    *,
    exclude_record_id: "str | int | None" = None,
    max_records: int = MAX_RECORDS,
    max_chars: int = MAX_CHARS,
) -> StyleExamples:
    """The titles and descriptions the user approved or edited on the
    *max_records* most recent records under *root* (the app's output dir),
    except record *exclude_record_id*, each kind capped at *max_chars*."""
    exclude = str(exclude_record_id) if exclude_record_id is not None else None
    found = []
    try:
        folders = [path for path in root.iterdir() if path.is_dir()]
    except OSError:
        return StyleExamples()
    for folder in folders:
        if exclude is not None and _record_id(folder) == exclude:
            continue
        when, title, description = _record_examples(folder)
        if title or description:
            found.append((when, title, description))
    found.sort(key=lambda item: item[0], reverse=True)
    recent = found[:max_records]
    return StyleExamples(
        titles=_fit([title for _, title, _ in recent if title], max_chars),
        descriptions=_fit([desc for _, _, desc in recent if desc], max_chars),
    )
