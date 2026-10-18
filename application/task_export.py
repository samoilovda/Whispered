"""Exporting reviewed tasks (N04): Markdown, CSV and iCalendar.

Qt-free. Calendar entries are made only for *accepted* tasks whose deadline
is written as a date or date-time; times are the user's local ("floating")
times — no time zone is invented. Nothing is sent anywhere.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone
from typing import Optional, Sequence

from domain.tasks import ACCEPTED, DISMISSED, DONE, TaskCard, parse_deadline

CSV_COLUMNS = ("task", "owner", "deadline", "status", "source_time", "source_quote", "origin")


def _clock(seconds: Optional[int]) -> str:
    if seconds is None:
        return ""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def to_markdown(cards: Sequence[TaskCard], unknown: str = "—") -> str:
    """A checklist; dismissed tasks are left out, finished ones ticked."""
    lines = []
    for card in cards:
        if card.status == DISMISSED:
            continue
        box = "x" if card.status == DONE else " "
        detail = [f"owner: {card.owner or unknown}", f"due: {card.deadline or unknown}"]
        if card.start is not None:
            detail.append(f"at {_clock(card.start)}")
        lines.append(f"- [{box}] {card.task} ({'; '.join(detail)})")
        if card.quote:
            lines.append(f"  > {card.quote}")
    return "\n".join(lines) + ("\n" if lines else "")


def to_csv(cards: Sequence[TaskCard]) -> str:
    """Every card, unknown values left empty (not "N/A")."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for card in cards:
        writer.writerow([
            card.task, card.owner or "", card.deadline or "", card.status,
            _clock(card.start), card.quote, card.origin,
        ])
    return out.getvalue()


def calendar_candidates(cards: Sequence[TaskCard]) -> list[TaskCard]:
    """Accepted tasks with a deadline that is actually a date."""
    return [c for c in cards if c.status == ACCEPTED and parse_deadline(c.deadline) is not None]


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def _fold(line: str) -> str:
    """RFC 5545 §3.1: lines over 75 octets are folded (never inside a
    UTF-8 character)."""
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    parts: list[str] = []
    current, size = "", 0
    for ch in line:
        width = len(ch.encode("utf-8"))
        if size + width > (75 if not parts else 74):
            parts.append(current)
            current, size = "", 0
        current += ch
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


def to_ics(cards: Sequence[TaskCard], now: Optional[datetime] = None) -> str:
    """A VCALENDAR of VTODOs for :func:`calendar_candidates`; ``""`` when
    there is nothing to put in it."""
    chosen = calendar_candidates(cards)
    if not chosen:
        return ""
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Whispered//Tasks//EN", "CALSCALE:GREGORIAN"]
    for card in chosen:
        due = parse_deadline(card.deadline)
        lines += ["BEGIN:VTODO", f"UID:{card.id}@whispered.local", f"DTSTAMP:{stamp}",
                  f"SUMMARY:{_escape(card.task)}"]
        if isinstance(due, datetime):
            lines.append("DUE:" + due.strftime("%Y%m%dT%H%M%S"))
        elif isinstance(due, date):
            lines.append("DUE;VALUE=DATE:" + due.strftime("%Y%m%d"))
        notes = []
        if card.owner:
            notes.append(f"Owner: {card.owner}")
        if card.start is not None:
            notes.append(f"In the recording at {_clock(card.start)}")
        if notes:
            lines.append("DESCRIPTION:" + _escape("\n".join(notes)))
        lines += ["STATUS:NEEDS-ACTION", "END:VTODO"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
