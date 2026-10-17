"""Shared cooperative cancellation contract for work below the UI layer."""

from typing import Callable


class OperationCancelled(RuntimeError):
    """Work was cancelled; its partial output must not be committed."""


def check_cancelled(is_cancelled: Callable[[], bool]) -> None:
    """Raise before doing more work or committing a result after cancellation."""
    if is_cancelled():
        raise OperationCancelled("Cancelled")
