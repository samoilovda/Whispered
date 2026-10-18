"""Reading a time the model gave for an insight item (shared by core's
text formatters and domain/tasks.py)."""

from __future__ import annotations

import re

_CLOCK_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")


def item_start(item) -> int | None:
    """An insight item's ``start`` in whole seconds, or ``None`` when the
    model gave none (a task often has no single moment). Accepts what a
    model plausibly returns: 312, 312.4, "312", "312s", "5:12", "1:05:12".
    Never invents a time."""
    if not isinstance(item, dict):
        return None
    value = item.get("start")
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        text = value.strip().lower().rstrip("s").strip()
        if text.isdigit():
            return int(text)
        match = _CLOCK_RE.match(text)
        if match:
            hours = int(match.group(1) or 0)
            return hours * 3600 + int(match.group(2)) * 60 + int(match.group(3))
    return None
