"""Tasks and decisions from a meeting, as reviewable cards (N04).

The insights step proposes action items. Here each becomes a card the user
can edit, accept, finish or dismiss. Nothing is guessed: an unknown owner
or date stays empty, a date is read only when it is written as one, and a
task typed by hand has no source quote and says so.

Cards live in the record's insights *overlay* (``insights.user.json``), never
in the model's own file. Regenerating insights keeps every card the user
touched; a touched card the model no longer produces is kept and marked
"not in the latest result" instead of being overwritten or silently dropped.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional, Sequence, Union

PROPOSED = "proposed"
ACCEPTED = "accepted"
DONE = "done"
DISMISSED = "dismissed"
STATUSES = (PROPOSED, ACCEPTED, DONE, DISMISSED)

MODEL = "model"
USER = "user"

TASKS_SCHEMA_VERSION = 1


def _normal(text: str) -> str:
    return " ".join((text or "").lower().split())


def model_task_id(task: str) -> str:
    """Stable id of a model-proposed task: its wording, not its position."""
    return "m-" + hashlib.sha1(_normal(task).encode("utf-8")).hexdigest()[:10]


@dataclass
class TaskCard:
    id: str
    task: str
    owner: Optional[str] = None
    deadline: Optional[str] = None      # as typed / proposed; read by parse_deadline()
    start: Optional[int] = None         # second of the source passage, if any
    quote: str = ""                     # the transcript text there (verified source)
    status: str = PROPOSED
    origin: str = MODEL
    edited: bool = False                # the user changed what the model proposed
    in_result: bool = True              # still produced by the latest insights
    extra: dict = field(default_factory=dict)

    @property
    def touched(self) -> bool:
        return self.origin == USER or self.edited or self.status != PROPOSED

    @property
    def has_source(self) -> bool:
        return bool(self.quote)

    def to_dict(self) -> dict:
        return {"id": self.id, "task": self.task, "owner": self.owner,
                "deadline": self.deadline, "start": self.start, "quote": self.quote,
                "status": self.status, "origin": self.origin, "edited": self.edited}

    @classmethod
    def from_dict(cls, data: dict) -> "TaskCard":
        status: str = str(data["status"]) if data.get("status") in STATUSES else PROPOSED
        return cls(
            id=str(data["id"]), task=str(data.get("task", "")),
            owner=data.get("owner") or None, deadline=data.get("deadline") or None,
            start=data.get("start") if isinstance(data.get("start"), int) else None,
            quote=str(data.get("quote") or ""), status=status,
            origin=USER if data.get("origin") == USER else MODEL,
            edited=bool(data.get("edited", False)), in_result=False,
        )


def new_user_card(task: str, existing_ids: Sequence[str] = ()) -> TaskCard:
    """A task typed by hand: no owner, date or source unless the user adds
    them — and it is shown as having no source."""
    n = 1
    while f"u-{n}" in existing_ids:
        n += 1
    return TaskCard(id=f"u-{n}", task=task.strip(), status=ACCEPTED, origin=USER, in_result=False)


def verify_source(segments: Sequence[Any], start: Optional[int]) -> str:
    """The transcript text at *start* — the card's checkable source — or
    ``""`` when the model gave no time or no passage lies there."""
    if start is None:
        return ""
    for seg in segments:
        if seg.start <= start <= seg.end + 1.0:
            return (seg.text or "").strip()
    return ""


def merge(
    model_items: Sequence[dict], stored: Sequence[TaskCard], segments: Sequence[Any] = (),
) -> list[TaskCard]:
    """The cards to show: the model's proposals with the user's work laid
    over them (see the module docstring)."""
    from domain.timestamps import item_start

    stored_by_id = {c.id: c for c in stored}
    cards: list[TaskCard] = []
    seen: set[str] = set()
    for item in model_items:
        if not isinstance(item, dict) or not str(item.get("task", "")).strip():
            continue
        task = str(item["task"]).strip()
        card_id = model_task_id(task)
        if card_id in seen:
            continue
        seen.add(card_id)
        start = item_start(item)
        saved = stored_by_id.get(card_id)
        if saved is not None:
            saved.in_result = True
            if saved.start is None and start is not None:
                saved.start = start
            if not saved.quote:
                saved.quote = verify_source(segments, saved.start)
            cards.append(saved)
            continue
        cards.append(TaskCard(
            id=card_id, task=task,
            owner=(str(item["owner"]).strip() or None) if item.get("owner") else None,
            deadline=(str(item["deadline"]).strip() or None) if item.get("deadline") else None,
            start=start, quote=verify_source(segments, start),
        ))
    for card in stored:
        if card.id in seen:
            continue
        if card.origin == USER:
            cards.append(card)            # always kept
        elif card.touched:
            card.in_result = False        # the model dropped it; the user's work stays
            cards.append(card)
    return cards


def only_to_store(cards: Sequence[TaskCard]) -> list[dict]:
    """What is worth keeping on disk: anything the user touched."""
    return [c.to_dict() for c in cards if c.touched]


def tasks_to_dict(cards: Sequence[TaskCard]) -> dict:
    return {"schema_version": TASKS_SCHEMA_VERSION, "cards": only_to_store(cards)}


def tasks_from_dict(data: Any) -> Optional[list[TaskCard]]:
    """``None`` for a newer schema (left alone, shown as unsupported)."""
    if not isinstance(data, dict):
        return []
    if int(data.get("schema_version", 1)) > TASKS_SCHEMA_VERSION:
        return None
    cards = []
    for entry in data.get("cards", []):
        try:
            cards.append(TaskCard.from_dict(entry))
        except (KeyError, TypeError, ValueError):
            continue
    return cards


# ------------------------------------------------------------------ dates

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}))?$")
_DOT = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?$")


def parse_deadline(text: Optional[str]) -> Union[date, datetime, None]:
    """A date (all-day) or a local date-time when *text* is written as one
    (``2026-11-03``, ``2026-11-03 15:30``, ``03.11.2026``); ``None`` for
    anything else — "Friday", "next week", "ASAP" are never turned into a
    date, because the meeting's own date is not known here."""
    if not text:
        return None
    value = text.strip()
    match = _ISO.match(value)
    if match:
        year, month, day, hour, minute = match.groups()
    else:
        match = _DOT.match(value)
        if not match:
            return None
        day, month, year, hour, minute = match.groups()
    try:
        if hour is None:
            return date(int(year), int(month), int(day))
        return datetime(int(year), int(month), int(day), int(hour), int(minute))
    except ValueError:
        return None
