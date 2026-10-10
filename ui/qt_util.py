"""Small typing helpers for PyQt6.

PyQt6's stubs mark many accessors as possibly ``None`` (``menu.addAction``,
``QApplication.clipboard``, ``widget.layout``…) where the call site knows
the object exists. :func:`must` states that once, instead of a ``None``
check that could never fire.
"""

from __future__ import annotations

from typing import Optional, TypeVar

T = TypeVar("T")


def must(value: Optional[T]) -> T:
    """*value*, which Qt types as optional but this call site knows is set.
    A ``None`` raises at once with a clear message rather than an
    ``AttributeError`` further on."""
    if value is None:
        raise RuntimeError("Qt returned None where an object was expected")
    return value
