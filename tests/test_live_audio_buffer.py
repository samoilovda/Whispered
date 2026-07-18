"""Unit tests for L4 live audio buffering; no microphone, model, or Qt."""

from __future__ import annotations

import threading

import pytest

from core.live import AudioFrame, BoundedAudioRing, CancellationToken, MonotonicTimestamp


def _frame(sequence: int, timestamp: float, payload: bytes = b"12") -> AudioFrame:
    return AudioFrame("mic", sequence, timestamp, timestamp, 16_000, payload)


def test_monotonic_timestamp_clamps_a_clock_reset():
    timestamps = iter((4.0, 3.0, 5.0))
    monotonic = MonotonicTimestamp(lambda: next(timestamps))

    assert [monotonic.now() for _ in range(3)] == [4.0, 4.0, 5.0]


def test_ring_is_bounded_and_marks_the_first_frame_after_a_drop():
    ring = BoundedAudioRing(capacity_frames=2, capacity_bytes=8)

    assert ring.put(_frame(0, 0.0, b"1234"))
    assert ring.put(_frame(1, 0.1, b"1234"))
    assert ring.put(_frame(2, 0.2, b"1234"))

    stats = ring.stats()
    assert (stats.queued_frames, stats.queued_bytes) == (2, 8)
    assert (stats.dropped_frames, stats.dropped_bytes) == (1, 4)
    assert stats.lag_seconds == pytest.approx(0.100125)

    first = ring.get()
    assert first is not None
    assert first.sequence == 1
    assert first.discontinuity is True
    next_frame = ring.get()
    assert next_frame is not None
    assert next_frame.sequence == 2
    assert next_frame.discontinuity is False
    assert ring.stats().popped_frames == 2


def test_oversized_frame_is_rejected_and_counted():
    ring = BoundedAudioRing(capacity_frames=2, capacity_bytes=3)

    assert ring.put(_frame(0, 0.0, b"1234")) is False
    stats = ring.stats()
    assert stats.queued_frames == 0
    assert (stats.offered_frames, stats.accepted_frames) == (1, 0)
    assert (stats.dropped_frames, stats.dropped_bytes) == (1, 4)


def test_close_drains_existing_frames_but_cancel_discards_immediately():
    ring = BoundedAudioRing(capacity_frames=2)
    ring.put(_frame(0, 0.0))
    ring.close()

    assert ring.get().sequence == 0
    assert ring.get() is None
    assert ring.put(_frame(1, 0.1)) is False

    cancelled = BoundedAudioRing(capacity_frames=2)
    cancelled.put(_frame(0, 0.0))
    cancelled.cancel()
    assert cancelled.get() is None
    assert cancelled.stats().cancelled is True


def test_waiting_consumer_observes_cancellation():
    ring = BoundedAudioRing(capacity_frames=2)
    token = CancellationToken()
    result: list[AudioFrame | None] = []

    waiter = threading.Thread(
        target=lambda: result.append(ring.get(cancellation=token)),
        daemon=True,
    )
    waiter.start()
    token.cancel()
    waiter.join(timeout=1.0)

    assert not waiter.is_alive()
    assert result == [None]
