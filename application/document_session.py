"""Single fan-out point for distributing a transcription result to every UI
consumer that needs it.

Extracted from ui/main_window.py (see docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md,
R6). Three call sites — a fresh transcription finishing, opening a history
record, and an in-place manual edit — each hand-maintained their own list of
"which panel gets told about this result." That drift is exactly how Cover
ended up missing segments on one of the three paths (fixed in the audit, but
by hand, one call site at a time, after the fact). ``apply_result()`` is now
the one place that list lives, so a panel added to it is automatically
covered on every path that produces or loads a result.
"""

from __future__ import annotations

from typing import Callable, List

from domain.transcription import TranscriptionResult

ResultConsumer = Callable[[TranscriptionResult], None]


class DocumentSession:
    """Owns the list of consumers that must see every new transcription
    result and applies a result to all of them in one call.

    Consumers are registered as plain callables rather than requiring
    panels to implement a shared interface — most panels expose slightly
    different methods (``set_segments(segments, transcript_language=...)``,
    ``set_transcript(text)``, ``set_result(result)``), so the owner
    registers a small adapter per panel instead of DocumentSession knowing
    about panel classes.
    """

    def __init__(self) -> None:
        self._consumers: List[ResultConsumer] = []

    def register_consumer(self, consumer: ResultConsumer) -> None:
        """Add a callable invoked with the result on every apply_result()."""
        self._consumers.append(consumer)

    def apply_result(self, result: TranscriptionResult) -> None:
        """Distribute *result* to every registered consumer, in
        registration order. Matches the previous per-call-site behavior of
        calling each panel in sequence — a consumer that raises still
        propagates, same as before this existed."""
        for consumer in self._consumers:
            consumer(result)
