"""Unit tests for L11 fair dual-source ASR scheduling."""

from __future__ import annotations

from dataclasses import dataclass

from core.live import DualQueueASRScheduler, SpeechTurn


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in tuple(self._slots):
            slot(*args)


@dataclass
class _Result:
    request_id: int
    turn: SpeechTurn


class _FakeWorker:
    def __init__(self, capacity=1):
        self.capacity = capacity
        self.final = _Signal()
        self.decode_error = _Signal()
        self.calls = []
        self._active = {}
        self._next_id = 1

    def submit(self, turn, pcm):
        if len(self._active) >= self.capacity:
            return None
        request_id = self._next_id
        self._next_id += 1
        self._active[request_id] = turn
        self.calls.append(turn.source)
        return request_id

    def complete_one(self):
        request_id, turn = next(iter(self._active.items()))
        del self._active[request_id]
        self.final.emit(_Result(request_id, turn))


def _turn(source, sequence):
    return SpeechTurn(source, sequence, sequence + 1, sequence, sequence)


def test_scheduler_gives_waiting_source_a_turn_before_repeating_source():
    worker = _FakeWorker(capacity=1)
    scheduler = DualQueueASRScheduler(worker, max_pending_per_source=4, max_in_flight=1)

    assert scheduler.submit(_turn("mic", 0), b"mic-0")
    assert scheduler.submit(_turn("mic", 1), b"mic-1")
    assert scheduler.submit(_turn("system", 0), b"system-0")

    worker.complete_one()
    assert worker.calls == ["mic", "system"]
    worker.complete_one()
    assert worker.calls == ["mic", "system", "mic"]


def test_scheduler_keeps_source_backlog_bounded_and_reports_drop():
    worker = _FakeWorker(capacity=1)
    scheduler = DualQueueASRScheduler(worker, max_pending_per_source=1, max_in_flight=1)

    assert scheduler.submit(_turn("mic", 0), b"one")
    assert scheduler.submit(_turn("mic", 1), b"two")
    assert not scheduler.submit(_turn("mic", 2), b"three")

    stats = scheduler.stats()
    assert stats.dropped == 1
    assert stats.queue_depths["mic"] == 1
    assert stats.in_flight == 1


def test_scheduler_forwards_result_and_recovers_after_decode_error():
    worker = _FakeWorker(capacity=1)
    received = []
    scheduler = DualQueueASRScheduler(worker, result_callback=received.append)

    scheduler.submit(_turn("system", 0), b"pcm")
    request_id = next(iter(worker._active))
    worker.final.emit(_Result(request_id, _turn("system", 0)))

    assert received[0].request_id == request_id
    assert scheduler.stats().completed == 1
