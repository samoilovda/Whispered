"""Fair dual-source scheduling for one persistent live ASR worker."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from core.live.contracts import SpeechTurn


@dataclass(frozen=True, slots=True)
class SchedulerStats:
    """Observable counters for the live scheduler."""

    queued: int
    dispatched: int
    completed: int
    failed: int
    dropped: int
    dropped_partials: int
    in_flight: int
    queue_depths: dict[str, int]
    dispatched_by_source: dict[str, int]
    max_wait_seconds: float


@dataclass(frozen=True, slots=True)
class _QueuedTurn:
    turn: SpeechTurn
    pcm: bytes
    enqueued_at: float
    final: bool


@dataclass(frozen=True, slots=True)
class _InFlight:
    source: str
    enqueued_at: float


class DualQueueASRScheduler:
    """Multiplex source queues onto one persistent worker without starvation.

    The worker remains the owner of the heavy model and its bounded process
    queue. This class only owns the per-source waiting queues and dispatch
    policy, so it can be tested with a tiny fake worker and does not alter the
    batch transcription path.
    """

    def __init__(
        self,
        worker: Any | None = None,
        *,
        max_pending_per_source: int = 16,
        max_in_flight: int = 1,
        fairness_quantum_seconds: float = 0.05,
        clock: Callable[[], float] = time.monotonic,
        result_callback: Callable[[Any], None] | None = None,
    ) -> None:
        if max_pending_per_source < 1:
            raise ValueError("max_pending_per_source must be positive")
        if max_in_flight < 1:
            raise ValueError("max_in_flight must be positive")
        if fairness_quantum_seconds < 0:
            raise ValueError("fairness_quantum_seconds must be non-negative")
        self.max_pending_per_source = max_pending_per_source
        self.max_in_flight = max_in_flight
        self.fairness_quantum_seconds = fairness_quantum_seconds
        self._clock = clock
        self._result_callback = result_callback
        self._queues: dict[str, deque[_QueuedTurn]] = {}
        self._in_flight: dict[int, _InFlight] = {}
        self._worker: Any | None = None
        self._last_source: str | None = None
        self._queued = 0
        self._dispatched = 0
        self._completed = 0
        self._failed = 0
        self._dropped = 0
        self._dropped_partials = 0
        self._max_wait_seconds = 0.0
        self._dispatched_by_source: dict[str, int] = {}
        if worker is not None:
            self.attach_worker(worker)

    def attach_worker(self, worker: Any) -> None:
        """Attach a worker exposing ``submit`` and result signals."""
        if self._worker is not None:
            raise RuntimeError("ASR worker is already attached")
        self._worker = worker
        worker.final.connect(self._on_result)
        partial_signal = getattr(worker, "partial", None)
        if partial_signal is not None:
            partial_signal.connect(self._on_result)
        detail_signal = getattr(worker, "decode_error_detail", None)
        if detail_signal is not None:
            detail_signal.connect(self._on_decode_error_detail)
        else:
            worker.decode_error.connect(self._on_decode_error)
        self.pump()

    def submit(self, turn: SpeechTurn, pcm: bytes, *, final: bool = True) -> bool:
        """Enqueue a turn, returning ``False`` only when its source is full."""
        if not isinstance(turn, SpeechTurn):
            raise TypeError("turn must be a SpeechTurn")
        source_queue = self._queues.setdefault(turn.source, deque())
        if final:
            retained = deque(
                item
                for item in source_queue
                if item.final or item.turn.first_sequence != turn.first_sequence
            )
            removed = len(source_queue) - len(retained)
            if removed:
                self._queued -= removed
                self._dropped_partials += removed
                source_queue = retained
                self._queues[turn.source] = source_queue
        if len(source_queue) >= self.max_pending_per_source:
            if final:
                partial_index = next(
                    (index for index, item in enumerate(source_queue) if not item.final),
                    None,
                )
                if partial_index is not None:
                    del source_queue[partial_index]
                    self._queued -= 1
                    self._dropped_partials += 1
                else:
                    self._dropped += 1
                    return False
            else:
                self._dropped_partials += 1
                return False
        source_queue.append(_QueuedTurn(turn, bytes(pcm), self._clock(), bool(final)))
        self._queued += 1
        self.pump()
        return True

    def pump(self, max_dispatch: int | None = None) -> int:
        """Dispatch as many waiting turns as the worker currently accepts."""
        if self._worker is None:
            return 0
        dispatched = 0
        while self._in_flight.__len__() < self.max_in_flight:
            if max_dispatch is not None and dispatched >= max_dispatch:
                break
            source = self._choose_source()
            if source is None:
                break
            item = self._queues[source][0]
            try:
                request_id = self._worker.submit(item.turn, item.pcm, final=item.final)
            except TypeError:
                request_id = self._worker.submit(item.turn, item.pcm)
            if request_id is None:
                break
            self._queues[source].popleft()
            self._queued -= 1
            self._in_flight[int(request_id)] = _InFlight(source, item.enqueued_at)
            self._last_source = source
            self._dispatched += 1
            self._dispatched_by_source[source] = self._dispatched_by_source.get(source, 0) + 1
            dispatched += 1
        self._update_max_wait()
        return dispatched

    def queued_for(self, source: str) -> int:
        """Return the waiting (not in-flight) depth for one source."""
        return len(self._queues.get(source, ()))

    def stats(self) -> SchedulerStats:
        self._update_max_wait()
        return SchedulerStats(
            queued=self._queued,
            dispatched=self._dispatched,
            completed=self._completed,
            failed=self._failed,
            dropped=self._dropped,
            dropped_partials=self._dropped_partials,
            in_flight=len(self._in_flight),
            queue_depths={source: len(queue) for source, queue in self._queues.items()},
            dispatched_by_source=dict(self._dispatched_by_source),
            max_wait_seconds=self._max_wait_seconds,
        )

    def clear_pending(self) -> int:
        """Discard waiting turns, leaving an already-running decode intact."""
        removed = sum(len(queue) for queue in self._queues.values())
        for source_queue in self._queues.values():
            source_queue.clear()
        self._queued -= removed
        return removed

    def _choose_source(self) -> str | None:
        available = [source for source, queue in self._queues.items() if queue]
        if not available:
            return None
        now = self._clock()
        waits = {
            source: max(0.0, now - self._queues[source][0].enqueued_at)
            for source in available
        }
        if self._last_source in waits and len(waits) > 1:
            other_waits = {
                source: wait for source, wait in waits.items() if source != self._last_source
            }
            oldest_other = max(other_waits.values())
            if waits[self._last_source] - oldest_other <= self.fairness_quantum_seconds:
                return sorted(
                    source for source in other_waits if other_waits[source] == oldest_other
                )[0]
        best_wait = max(waits.values())
        candidates = [source for source in available if waits[source] == best_wait]
        if len(candidates) > 1 and self._last_source in candidates:
            candidates.remove(self._last_source)
        return sorted(candidates)[0]

    def _on_result(self, result: Any) -> None:
        request_id = int(result.request_id)
        self._in_flight.pop(request_id, None)
        self._completed += 1
        if self._result_callback is not None:
            self._result_callback(result)
        self.pump()

    def _on_decode_error_detail(self, payload: Any) -> None:
        request_id, message = payload
        self._in_flight.pop(int(request_id), None)
        self._failed += 1
        del message
        self.pump()

    def _on_decode_error(self, message: str) -> None:
        """Compatibility fallback for workers without request-aware errors."""
        del message
        self._failed += 1
        self.pump()

    def _update_max_wait(self) -> None:
        now = self._clock()
        waits = [
            max(0.0, now - queue[0].enqueued_at)
            for queue in self._queues.values()
            if queue
        ]
        self._max_wait_seconds = max(waits, default=0.0)
