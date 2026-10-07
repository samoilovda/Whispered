"""Human dates for lists: relative groups and short, localized stamps.

Qt-free so the rules are unit-testable with system python. Month names
come from the locale files (``month_short_N`` / ``month_full_N``) rather
than ``strftime("%b")``, which follows the process C locale — a Russian
interface showed "07 Oct 2026".
"""

from __future__ import annotations

from datetime import date, datetime

from core.i18n import tr

# Group keys, newest first. Anything older than a month is grouped by
# calendar month: "YYYY-MM".
GROUP_TODAY = "today"
GROUP_YESTERDAY = "yesterday"
GROUP_WEEK = "week"
GROUP_MONTH = "month"


def parse_iso(stamp: str) -> datetime | None:
    """Parse a stored ISO timestamp into local time; ``None`` if unreadable."""
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def date_group(when: datetime, now: datetime) -> str:
    """The list group *when* falls into, relative to *now* (both local)."""
    days = (now.date() - when.date()).days
    if days <= 0:
        return GROUP_TODAY
    if days == 1:
        return GROUP_YESTERDAY
    if days < 7:
        return GROUP_WEEK
    if days < 30:
        return GROUP_MONTH
    return f"{when.year:04d}-{when.month:02d}"


def group_label(key: str, now: datetime | None = None) -> str:
    """Display text for a ``date_group`` key."""
    if key == GROUP_TODAY:
        return tr("date_today")
    if key == GROUP_YESTERDAY:
        return tr("date_yesterday")
    if key == GROUP_WEEK:
        return tr("date_last_7_days")
    if key == GROUP_MONTH:
        return tr("date_last_30_days")
    try:
        year_s, month_s = key.split("-")
        year, month = int(year_s), int(month_s)
    except ValueError:
        return key
    current_year = (now or datetime.now()).year
    name = tr(f"month_full_{month}")
    return name if year == current_year else tr("date_month_year", month=name, year=year)


def _day_month(day: date) -> str:
    return tr("date_day_month", day=day.day, month=tr(f"month_short_{day.month}"))


def short_stamp(when: datetime, now: datetime) -> str:
    """Compact stamp for a list row: time only for today and yesterday
    (the group header already says which day), day + month + time this
    year, day + month + year before that."""
    clock = f"{when.hour:02d}:{when.minute:02d}"
    days = (now.date() - when.date()).days
    if days in (0, 1):
        return clock
    if when.year == now.year:
        return f"{_day_month(when.date())}, {clock}"
    return f"{_day_month(when.date())} {when.year}"


def relative_stamp(when: datetime, now: datetime) -> str:
    """Like short_stamp, but self-contained for a list without date
    headers: "Today, 11:36" / "Yesterday, 18:12", else short_stamp."""
    days = (now.date() - when.date()).days
    clock = f"{when.hour:02d}:{when.minute:02d}"
    if days == 0:
        return f"{tr('date_today')}, {clock}"
    if days == 1:
        return f"{tr('date_yesterday')}, {clock}"
    return short_stamp(when, now)
