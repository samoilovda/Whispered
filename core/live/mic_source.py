"""Live microphone source adapter built on the existing recorder."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from config import get_config
from core.live.audio_buffer import BoundedAudioRing, MonotonicTimestamp, RingStats
from core.live.contracts import AudioFrame
from core.recorder import Recorder


class MicSource(QObject):
    """Expose recorder audio frames without changing the legacy recorder API.

    The adapter is opt-in. With ``enabled=False`` it does not start and does
    not install a frame sink, so existing Record/Pause/Resume/Stop callers
    continue to use ``Recorder`` exactly as before. When enabled, the
    recorder still owns WAV writing and level calculation; this class only
    adds timestamped frames to a bounded ring for live consumers. Live uses
    the recorder's capture-only mode by default, so no WAV is created.
    """

    level_changed = pyqtSignal(float)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        parent=None,
        *,
        source: str = "mic",
        enabled: bool | None = None,
        device: Optional[int] = None,
        capacity_frames: int = 100,
        capacity_bytes: int | None = None,
        persist_audio: bool = False,
        recorder_factory: Callable = Recorder,
    ) -> None:
        super().__init__(parent)
        if not source:
            raise ValueError("source must not be empty")

        self.source = source
        self.enabled = (
            get_config().live_transcription_enabled if enabled is None else enabled
        )
        self.device = device
        self._capacity_frames = capacity_frames
        self._capacity_bytes = capacity_bytes
        self._ring = self._new_ring()
        self._ring.close()
        self._clock = MonotonicTimestamp()
        self._sequence = 0
        self._accept_frames = False
        self._resume_discontinuity = False
        self.persist_audio = bool(persist_audio)

        frame_sink = self._capture_frame if self.enabled else None
        self._recorder = recorder_factory(
            parent,
            frame_sink=frame_sink,
            write_wav=self.persist_audio,
        )
        self._recorder.level_changed.connect(self._forward_level)
        self._recorder.error_occurred.connect(self.error_occurred.emit)

    @property
    def recorder(self) -> Recorder:
        """Return the wrapped recorder for diagnostics and legacy controls."""
        return self._recorder

    @property
    def elapsed_seconds(self) -> float:
        return self._recorder.elapsed_seconds

    def start(self, device: Optional[int] = None) -> bool:
        """Start the wrapped recorder and return whether it actually started."""
        if not self.enabled:
            self.error_occurred.emit(
                "Live transcription is disabled in Settings; enable it before starting."
            )
            return False
        if self._recorder.is_recording():
            return True

        self._ring = self._new_ring()
        self._clock = MonotonicTimestamp()
        self._sequence = 0
        self._accept_frames = True
        self._resume_discontinuity = False
        self._recorder.start(self.device if device is None else device)
        if not self._recorder.is_recording():
            self._accept_frames = False
            self._ring.cancel()
            return False
        return True

    def pause(self) -> None:
        """Pause recording; the wrapped recorder keeps its legacy semantics."""
        self._recorder.pause()
        self._resume_discontinuity = True

    def resume(self) -> None:
        """Resume recording after pause."""
        self._recorder.resume()

    def stop(self) -> Optional[str]:
        """Stop capture, close the frame stream, and return an optional WAV path."""
        self._accept_frames = False
        path = self._recorder.stop()
        self._ring.close()
        return path

    def cancel(self) -> Optional[str]:
        """Stop the recorder and discard queued frames immediately."""
        self._accept_frames = False
        self._ring.cancel()
        return self._recorder.stop()

    def is_recording(self) -> bool:
        return self._recorder.is_recording()

    def get_frame(self, timeout: float | None = None) -> AudioFrame | None:
        """Return the next live frame, or ``None`` after Stop/Cancel."""
        return self._ring.get(timeout=timeout)

    def stats(self) -> RingStats:
        """Return queue/drop/lag metrics for the live status UI."""
        return self._ring.stats()

    def _capture_frame(self, pcm: bytes, _frames: int, time_info) -> None:
        if not self._accept_frames:
            return

        monotonic_timestamp = self._clock.now()
        source_timestamp = _input_timestamp(time_info, monotonic_timestamp)
        frame = AudioFrame(
            source=self.source,
            sequence=self._sequence,
            source_timestamp=source_timestamp,
            monotonic_timestamp=monotonic_timestamp,
            sample_rate=16_000,
            pcm=pcm,
            discontinuity=self._resume_discontinuity,
        )
        self._resume_discontinuity = False
        self._sequence += 1
        self._ring.put(frame)

    def _forward_level(self, level: float) -> None:
        if self._accept_frames:
            self.level_changed.emit(level)

    def _new_ring(self) -> BoundedAudioRing:
        return BoundedAudioRing(self._capacity_frames, self._capacity_bytes)


def _input_timestamp(time_info, fallback: float) -> float:
    """Read PortAudio's input timestamp, falling back to monotonic time."""
    value = None
    if isinstance(time_info, dict):
        value = time_info.get("inputBufferAdcTime")
    else:
        value = getattr(time_info, "inputBufferAdcTime", None)
    try:
        if value is None:
            return fallback
        value = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(value) or value < 0:
        return fallback
    return value
