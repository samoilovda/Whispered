"""Per-source voice-activity detection for the live audio pipeline.

The state machine is deliberately independent of Qt, sounddevice, and ASR.
Its default backend is a small PCM-energy detector, which keeps the live core
usable without adding an optional ML dependency. A Silero/WebRTC backend can
be supplied later through the same ``speech_detector`` callable.
"""

from __future__ import annotations

import math
from array import array
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Deque

from core.live.contracts import AudioFrame, SpeechTurn


@dataclass(frozen=True, slots=True)
class VADConfig:
    """Timing and energy parameters for one source."""

    speech_threshold: float = 0.02
    silence_threshold: float = 0.01
    end_silence_seconds: float = 0.6
    pre_roll_seconds: float = 0.2
    post_roll_seconds: float = 0.2
    max_turn_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not 0 < self.silence_threshold <= self.speech_threshold <= 1:
            raise ValueError("VAD thresholds must satisfy 0 < silence <= speech <= 1")
        for name in (
            "end_silence_seconds",
            "pre_roll_seconds",
            "post_roll_seconds",
            "max_turn_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


class EnergyVAD:
    """Dependency-free int16 PCM detector used as the default backend."""

    def __init__(self, threshold: float = 0.02) -> None:
        if not 0 < threshold <= 1:
            raise ValueError("threshold must be in (0, 1]")
        self.threshold = threshold

    def __call__(self, frame: AudioFrame) -> bool:
        return pcm_rms(frame.pcm) >= self.threshold


class PerSourceVAD:
    """Turn timestamped frames from exactly one source into ``SpeechTurn``s.

    ``feed`` is constant-work apart from the bounded pre-roll history. It
    returns completed turns only; a caller can keep feeding frames while ASR
    works elsewhere. ``flush`` closes the active turn on Stop.
    """

    def __init__(
        self,
        source: str,
        config: VADConfig | None = None,
        speech_detector: Callable[[AudioFrame], bool] | None = None,
    ) -> None:
        if not source:
            raise ValueError("source must not be empty")
        self.source = source
        self.config = config or VADConfig()
        self._speech_detector = speech_detector or EnergyVAD(self.config.speech_threshold)
        self._history: Deque[AudioFrame] = deque()
        self._active_frames: list[AudioFrame] = []
        self._last_speech_end: float | None = None
        self._silence_seconds = 0.0
        self._turn_discontinuity = False
        self._last_timestamp: float | None = None

    def feed(self, frame: AudioFrame) -> list[SpeechTurn]:
        """Consume one frame and return any turns completed by it."""
        if frame.source != self.source:
            raise ValueError(
                f"PerSourceVAD is for {self.source!r}, got {frame.source!r}"
            )

        frame_start = frame.monotonic_timestamp
        frame_end = frame_start + _frame_duration(frame)
        out: list[SpeechTurn] = []
        out_of_order = (
            self._last_timestamp is not None and frame_start < self._last_timestamp
        )
        self._last_timestamp = max(frame_start, self._last_timestamp or frame_start)

        if frame.discontinuity or out_of_order:
            if self._active_frames:
                out.append(
                    self._finish(
                        end=self._turn_end(frame_start),
                        discontinuity=True,
                    )
                )
            self._reset_active()
            self._history.clear()
            self._turn_discontinuity = True

        is_speech = self._is_speech(frame)
        if not self._active_frames:
            self._remember(frame)
            if is_speech:
                self._active_frames = list(self._history)
                self._last_speech_end = frame_end
                self._silence_seconds = 0.0
            return out

        if is_speech:
            self._active_frames.append(frame)
            self._last_speech_end = frame_end
            self._silence_seconds = 0.0
        else:
            self._silence_seconds += _frame_duration(frame)
            if self._last_speech_end is not None:
                post_roll_end = self._last_speech_end + self.config.post_roll_seconds
                if frame_start <= post_roll_end:
                    self._active_frames.append(frame)
            if self._silence_seconds >= self.config.end_silence_seconds:
                end = self._turn_end(frame_end)
                out.append(self._finish(end=end, discontinuity=self._turn_discontinuity))
                self._reset_active()
                self._history.clear()
                return out

        if self._active_duration(frame_end) >= self.config.max_turn_seconds:
            out.append(self._finish(end=frame_end, discontinuity=self._turn_discontinuity))
            self._reset_active()
            self._history.clear()
        return out

    def flush(self) -> list[SpeechTurn]:
        """Finalize the active turn, if any, at the end of the available audio."""
        if not self._active_frames:
            return []
        turn = self._finish(
            end=self._turn_end(self._active_end()),
            discontinuity=self._turn_discontinuity,
        )
        self._reset_active()
        self._history.clear()
        return [turn]

    def reset(self) -> None:
        """Discard active state, typically after source restart."""
        self._active_frames.clear()
        self._history.clear()
        self._last_speech_end = None
        self._silence_seconds = 0.0
        self._turn_discontinuity = False
        self._last_timestamp = None

    def _is_speech(self, frame: AudioFrame) -> bool:
        detected = bool(self._speech_detector(frame))
        if not self._active_frames:
            return detected
        if detected:
            return True
        return pcm_rms(frame.pcm) >= self.config.silence_threshold

    def _remember(self, frame: AudioFrame) -> None:
        self._history.append(frame)
        cutoff = frame.monotonic_timestamp - self.config.pre_roll_seconds
        while len(self._history) > 1 and self._history[0].monotonic_timestamp < cutoff:
            self._history.popleft()

    def _active_duration(self, frame_end: float) -> float:
        if not self._active_frames:
            return 0.0
        return frame_end - self._active_frames[0].monotonic_timestamp

    def _active_end(self) -> float:
        last = self._active_frames[-1]
        return last.monotonic_timestamp + _frame_duration(last)

    def _turn_end(self, available_end: float) -> float:
        if self._last_speech_end is None:
            return available_end
        return min(available_end, self._last_speech_end + self.config.post_roll_seconds)

    def _finish(self, end: float | None = None, discontinuity: bool = False) -> SpeechTurn:
        first = self._active_frames[0]
        last = self._active_frames[-1]
        start = first.monotonic_timestamp
        bounded_end = max(start, end if end is not None else last.monotonic_timestamp)
        return SpeechTurn(
            source=self.source,
            start=start,
            end=bounded_end,
            first_sequence=first.sequence,
            last_sequence=last.sequence,
            discontinuity=discontinuity,
        )

    def _reset_active(self) -> None:
        self._active_frames.clear()
        self._last_speech_end = None
        self._silence_seconds = 0.0
        self._turn_discontinuity = False


def pcm_rms(pcm: bytes) -> float:
    """Return normalized RMS for little-endian/int16 mono PCM."""
    if not pcm:
        return 0.0
    if len(pcm) % 2:
        raise ValueError("PCM int16 payload must contain an even number of bytes")

    samples = array("h")
    samples.frombytes(pcm)
    if not samples:
        return 0.0
    # sounddevice supplies native-endian int16; on the supported platforms
    # that is little-endian. Keep the fallback dependency-free and explicit.
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    return min(1.0, math.sqrt(mean_square) / 32768.0)


def _frame_duration(frame: AudioFrame) -> float:
    bytes_per_sample = 2
    return len(frame.pcm) / (bytes_per_sample * frame.sample_rate)
