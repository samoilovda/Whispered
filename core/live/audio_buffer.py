"""Bounded, cancellable audio buffering for live sources.

The audio callback must never wait for ASR or disk I/O.  ``BoundedAudioRing``
therefore accepts a frame in constant time, evicts the oldest backlog when it
is full, and exposes the eviction as metrics plus a discontinuity marker on
the next frame consumed by downstream processing.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable, Deque

from core.live.contracts import AudioFrame


@dataclass(frozen=True, slots=True)
class RingStats:
    """Point-in-time health metrics for one source ring."""

    capacity_frames: int
    capacity_bytes: int
    queued_frames: int
    queued_bytes: int
    offered_frames: int
    accepted_frames: int
    popped_frames: int
    dropped_frames: int
    dropped_bytes: int
    backlog_seconds: float
    oldest_timestamp: float | None
    newest_timestamp: float | None
    closed: bool
    cancelled: bool

    @property
    def lag_seconds(self) -> float:
        """Audio backlog duration, used as the user-visible lag metric."""
        return self.backlog_seconds


class CancellationToken:
    """Small thread-safe cancellation primitive shared by live workers."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait until cancelled and return whether cancellation happened."""
        return self._event.wait(timeout)


class MonotonicTimestamp:
    """Make observed timestamps non-decreasing across callback glitches.

    Normally callers use ``now()`` with the platform monotonic clock.  Passing
    an observed value is useful for adapters that receive a timestamp from an
    audio API: a clock reset is clamped to the last value instead of moving
    the live timeline backwards.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last: float | None = None
        self._lock = threading.Lock()

    def now(self, observed: float | None = None) -> float:
        value = self._clock() if observed is None else float(observed)
        if not math.isfinite(value) or value < 0:
            raise ValueError("timestamp must be a finite non-negative number")

        with self._lock:
            if self._last is not None and value < self._last:
                value = self._last
            self._last = value
            return value


class BoundedAudioRing:
    """Thread-safe non-blocking producer / cancellable consumer ring.

    ``put`` never waits, making it safe for a PortAudio callback.  When the
    ring is full, the oldest frames are discarded so the consumer eventually
    catches up to live audio rather than processing an ever older recording.
    Both frame count and PCM bytes are bounded; a frame larger than the byte
    limit is rejected and counted as a drop.
    """

    _DEFAULT_BYTES_PER_FRAME = 64 * 1024
    _CANCELLATION_POLL_SECONDS = 0.1

    def __init__(
        self,
        capacity_frames: int = 100,
        capacity_bytes: int | None = None,
    ) -> None:
        if capacity_frames < 1:
            raise ValueError("capacity_frames must be positive")
        if capacity_bytes is None:
            capacity_bytes = capacity_frames * self._DEFAULT_BYTES_PER_FRAME
        if capacity_bytes < 1:
            raise ValueError("capacity_bytes must be positive")

        self.capacity_frames = capacity_frames
        self.capacity_bytes = capacity_bytes
        self._items: Deque[AudioFrame] = deque()
        self._queued_bytes = 0
        self._offered_frames = 0
        self._accepted_frames = 0
        self._popped_frames = 0
        self._dropped_frames = 0
        self._dropped_bytes = 0
        self._pending_discontinuity = False
        self._closed = False
        self._cancelled = False
        self._condition = threading.Condition()

    def put(self, frame: AudioFrame) -> bool:
        """Offer *frame* without waiting; return whether it was retained."""
        if not isinstance(frame, AudioFrame):
            raise TypeError("BoundedAudioRing accepts AudioFrame instances")

        frame_bytes = len(frame.pcm)
        with self._condition:
            self._offered_frames += 1
            if self._closed or self._cancelled:
                self._record_drop(frame_bytes)
                return False
            if frame_bytes > self.capacity_bytes:
                self._record_drop(frame_bytes)
                self._pending_discontinuity = True
                return False

            while (
                len(self._items) >= self.capacity_frames
                or self._queued_bytes + frame_bytes > self.capacity_bytes
            ):
                evicted = self._items.popleft()
                self._queued_bytes -= len(evicted.pcm)
                self._record_drop(len(evicted.pcm))
                self._pending_discontinuity = True

            self._items.append(frame)
            self._queued_bytes += frame_bytes
            self._accepted_frames += 1
            self._condition.notify()
            return True

    def get(
        self,
        timeout: float | None = None,
        cancellation: CancellationToken | threading.Event | None = None,
    ) -> AudioFrame | None:
        """Return the oldest frame, or ``None`` on close, timeout, or cancel."""
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative or None")
        deadline = None if timeout is None else time.monotonic() + timeout

        with self._condition:
            if self._cancelled or _is_cancelled(cancellation):
                return None
            while not self._items:
                if self._cancelled or _is_cancelled(cancellation):
                    return None
                if self._closed:
                    return None

                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                if cancellation is not None:
                    remaining = _minimum_timeout(remaining, self._CANCELLATION_POLL_SECONDS)
                self._condition.wait(remaining)

            frame = self._items.popleft()
            self._queued_bytes -= len(frame.pcm)
            self._popped_frames += 1
            if self._pending_discontinuity:
                frame = replace(frame, discontinuity=True)
                self._pending_discontinuity = False
            return frame

    def close(self) -> None:
        """Stop accepting frames while allowing the consumer to drain."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def cancel(self) -> None:
        """Abort immediately and discard queued audio to unblock consumers."""
        with self._condition:
            if not self._cancelled:
                for frame in self._items:
                    self._record_drop(len(frame.pcm))
                self._items.clear()
                self._queued_bytes = 0
            self._cancelled = True
            self._closed = True
            self._condition.notify_all()

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def __len__(self) -> int:
        with self._condition:
            return len(self._items)

    def stats(self) -> RingStats:
        """Return a consistent snapshot suitable for a meter/status panel."""
        with self._condition:
            timestamps = [item.monotonic_timestamp for item in self._items]
            oldest = timestamps[0] if timestamps else None
            newest = timestamps[-1] if timestamps else None
            backlog = 0.0 if oldest is None else max(0.0, newest - oldest)
            return RingStats(
                capacity_frames=self.capacity_frames,
                capacity_bytes=self.capacity_bytes,
                queued_frames=len(self._items),
                queued_bytes=self._queued_bytes,
                offered_frames=self._offered_frames,
                accepted_frames=self._accepted_frames,
                popped_frames=self._popped_frames,
                dropped_frames=self._dropped_frames,
                dropped_bytes=self._dropped_bytes,
                backlog_seconds=backlog,
                oldest_timestamp=oldest,
                newest_timestamp=newest,
                closed=self._closed,
                cancelled=self._cancelled,
            )

    def _record_drop(self, frame_bytes: int) -> None:
        self._dropped_frames += 1
        self._dropped_bytes += frame_bytes


def _is_cancelled(token: CancellationToken | threading.Event | None) -> bool:
    if token is None:
        return False
    if isinstance(token, CancellationToken):
        return token.is_cancelled
    return token.is_set()


def _minimum_timeout(left: float | None, right: float) -> float:
    if left is None:
        return right
    return min(left, right)
