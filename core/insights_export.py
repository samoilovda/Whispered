"""Plain-text formatters for saving generated Insights to disk.

Mirrors the UI rendering in ui/insights_panel.py's _ChapterRow/_ActionRow/
_MomentRow closely enough that the saved file reads the same as the panel,
without pulling in Qt — same split as core/youtube_description.py (a
Qt-free formatter used by ui/youtube_panel.py).
"""

from __future__ import annotations

from core.i18n import tr
from utils import format_duration


def format_chapters_text(chapters: list) -> str:
    """One line per chapter: ``MM:SS  Title``."""
    lines = []
    for item in chapters:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("start", 0))
        except (TypeError, ValueError):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        lines.append(f"{format_duration(start)}  {title}")
    return "\n".join(lines)


def format_action_items_text(items: list) -> str:
    """One entry per action item, matching _ActionRow's inline format."""
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        task = str(item.get("task", "")).strip()
        if not task:
            continue
        parts = [f"• {task}"]
        owner = item.get("owner")
        if owner:
            parts.append(f"  {tr('insights_owner')} {owner}")
        deadline = item.get("deadline")
        if deadline:
            parts.append(f"  {tr('insights_deadline')} {deadline}")
        lines.append("".join(parts))
    return "\n".join(lines)


def format_key_moments_text(moments: list) -> str:
    """One block per moment: timestamp + quote, with an optional note line."""
    blocks = []
    for item in moments:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("start", 0))
        except (TypeError, ValueError):
            continue
        quote = str(item.get("quote", "")).strip()
        if not quote:
            continue
        block = f'{format_duration(start)}  "{quote}"'
        note = str(item.get("note", "")).strip()
        if note:
            block += f"\n    {note}"
        blocks.append(block)
    return "\n\n".join(blocks)


_FORMATTERS = {
    "chapters": format_chapters_text,
    "action_items": format_action_items_text,
    "key_moments": format_key_moments_text,
}


def format_insight_text(insight_type: str, data: list) -> str:
    """Dispatch to the formatter for *insight_type*. Returns "" for an
    unknown type or non-list data (e.g. a raw-fallback string the LLM
    returned unparsed) rather than raising — callers decide whether an
    empty result is worth writing to disk."""
    formatter = _FORMATTERS.get(insight_type)
    if formatter is None or not isinstance(data, list):
        return ""
    return formatter(data)
