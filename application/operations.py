"""The background operations running at once, each with a name and its own
way to stop. Several can overlap (a clean-up, a book, a project export, the
queue); Cancel must address one of them by name, not "whatever the priority
list finds first". Qt-free: the window builds the list from what is running
and asks this module to describe it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence


@dataclass(frozen=True)
class Operation:
    key: str                       # stable id: "clean", "book", "project", …
    label: str                     # what the user calls it
    cancel: Callable[[], None]     # stops this operation only
    ai: bool = False               # reported as "AI processing cancelled"


def cancel_one(operations: Sequence[Operation], key: str) -> Operation | None:
    """Cancel the operation named *key*; the others are not touched."""
    found = next((op for op in operations if op.key == key), None)
    if found is not None:
        found.cancel()
    return found


def cancel_all(operations: Sequence[Operation]) -> None:
    for op in operations:
        op.cancel()


def describe(operations: Sequence[Operation]) -> str:
    """One line naming everything that is running."""
    return " · ".join(op.label for op in operations)
