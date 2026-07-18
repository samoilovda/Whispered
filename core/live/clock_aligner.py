"""Resampling and clock-drift metrics for one or more live sources."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from core.live.contracts import AudioFrame


@dataclass(frozen=True, slots=True)
class ClockMetrics:
    """Clock and resampling counters for one source."""

    source: str
    input_frames: int
    input_samples: int
    output_samples: int
    audio_duration_seconds: float
    source_elapsed_seconds: float
    monotonic_elapsed_seconds: float
    corrected_elapsed_seconds: float
    source_drift_seconds: float
    monotonic_drift_seconds: float
    corrected_drift_seconds: float
    drift_ppm: float
    correction_ppm: float
    discontinuities: int


@dataclass(frozen=True, slots=True)
class DriftReport:
    """Combined drift status for all sources seen by an aligner."""

    duration_seconds: float
    per_source: dict[str, ClockMetrics]
    max_abs_drift_seconds: float
    max_abs_drift_ppm: float
    within_target: bool


class SourceClockAligner:
    """Normalize one source to target PCM and continuously measure drift."""

    def __init__(
        self,
        source: str,
        target_sample_rate: int = 16_000,
        max_correction_ppm: float = 2_000.0,
    ) -> None:
        if not source:
            raise ValueError("source must not be empty")
        if target_sample_rate < 1:
            raise ValueError("target_sample_rate must be positive")
        if max_correction_ppm < 0:
            raise ValueError("max_correction_ppm must be non-negative")
        self.source = source
        self.target_sample_rate = target_sample_rate
        self.max_correction_ppm = max_correction_ppm
        self._first_source_timestamp: float | None = None
        self._first_monotonic_timestamp: float | None = None
        self._last_source_timestamp: float | None = None
        self._last_monotonic_timestamp: float | None = None
        self._input_frames = 0
        self._input_samples = 0
        self._output_samples = 0
        self._audio_duration = 0.0
        self._last_input_duration = 0.0
        self._correction_ppm = 0.0
        self._discontinuities = 0

    def process(self, frame: AudioFrame) -> AudioFrame:
        """Resample *frame*, apply bounded clock correction, and update metrics."""
        if frame.source != self.source:
            raise ValueError(f"Aligner is for {self.source!r}, got {frame.source!r}")
        _validate_timestamp(frame.source_timestamp, "source_timestamp")
        _validate_timestamp(frame.monotonic_timestamp, "monotonic_timestamp")
        if len(frame.pcm) % 2:
            raise ValueError("AudioFrame PCM must contain int16 samples")

        discontinuity = frame.discontinuity
        if (
            self._last_source_timestamp is not None
            and frame.source_timestamp < self._last_source_timestamp
        ) or (
            self._last_monotonic_timestamp is not None
            and frame.monotonic_timestamp < self._last_monotonic_timestamp
        ):
            discontinuity = True
        if discontinuity:
            self._discontinuities += 1

        if self._first_source_timestamp is None or discontinuity:
            if self._first_source_timestamp is None:
                self._first_source_timestamp = frame.source_timestamp
                self._first_monotonic_timestamp = frame.monotonic_timestamp

        sample_count = len(frame.pcm) // 2
        input_duration = sample_count / frame.sample_rate
        correction_ppm = self._estimate_correction(frame.source_timestamp, input_duration)
        output = _resample_int16(
            frame.pcm,
            frame.sample_rate,
            self.target_sample_rate,
            correction_ppm,
        )
        output_samples = len(output) // 2

        self._input_frames += 1
        self._input_samples += sample_count
        self._output_samples += output_samples
        self._audio_duration += input_duration
        self._last_input_duration = input_duration
        self._correction_ppm = correction_ppm
        self._last_source_timestamp = frame.source_timestamp
        self._last_monotonic_timestamp = frame.monotonic_timestamp

        return AudioFrame(
            source=frame.source,
            sequence=frame.sequence,
            source_timestamp=frame.source_timestamp,
            monotonic_timestamp=frame.monotonic_timestamp,
            sample_rate=self.target_sample_rate,
            pcm=output,
            discontinuity=discontinuity,
        )

    def metrics(self) -> ClockMetrics:
        source_elapsed = self._elapsed(self._first_source_timestamp, self._last_source_timestamp)
        source_elapsed += self._last_input_duration
        monotonic_elapsed = self._elapsed(
            self._first_monotonic_timestamp, self._last_monotonic_timestamp
        )
        monotonic_elapsed += self._last_input_duration
        corrected_elapsed = self._output_samples / self.target_sample_rate
        return ClockMetrics(
            source=self.source,
            input_frames=self._input_frames,
            input_samples=self._input_samples,
            output_samples=self._output_samples,
            audio_duration_seconds=self._audio_duration,
            source_elapsed_seconds=source_elapsed,
            monotonic_elapsed_seconds=monotonic_elapsed,
            corrected_elapsed_seconds=corrected_elapsed,
            source_drift_seconds=source_elapsed - self._audio_duration,
            monotonic_drift_seconds=monotonic_elapsed - self._audio_duration,
            corrected_drift_seconds=source_elapsed - corrected_elapsed,
            drift_ppm=_ppm(source_elapsed, self._audio_duration),
            correction_ppm=self._correction_ppm,
            discontinuities=self._discontinuities,
        )

    def reset(self) -> None:
        """Reset clock baselines after a source/device restart."""
        self.__init__(self.source, self.target_sample_rate, self.max_correction_ppm)

    @staticmethod
    def _elapsed(first: float | None, last: float | None) -> float:
        if first is None or last is None:
            return 0.0
        return max(0.0, last - first)

    def _estimate_correction(self, current_timestamp: float, current_duration: float) -> float:
        if self._first_source_timestamp is None or self._last_source_timestamp is None:
            return 0.0
        source_elapsed = max(
            0.0,
            current_timestamp - self._first_source_timestamp,
        ) + current_duration
        audio_duration = self._audio_duration + current_duration
        raw_ppm = _ppm(source_elapsed, audio_duration)
        return max(-self.max_correction_ppm, min(self.max_correction_ppm, raw_ppm))


class DualSourceClockAligner:
    """Keep independent source clocks while producing a comparable report."""

    def __init__(
        self,
        target_sample_rate: int = 16_000,
        max_correction_ppm: float = 2_000.0,
        drift_target_seconds: float = 0.1,
    ) -> None:
        if drift_target_seconds < 0:
            raise ValueError("drift_target_seconds must be non-negative")
        self.target_sample_rate = target_sample_rate
        self.max_correction_ppm = max_correction_ppm
        self.drift_target_seconds = drift_target_seconds
        self._aligners: dict[str, SourceClockAligner] = {}

    def process(self, frame: AudioFrame) -> AudioFrame:
        aligner = self._aligners.setdefault(
            frame.source,
            SourceClockAligner(
                frame.source,
                target_sample_rate=self.target_sample_rate,
                max_correction_ppm=self.max_correction_ppm,
            ),
        )
        return aligner.process(frame)

    def metrics(self) -> dict[str, ClockMetrics]:
        return {source: aligner.metrics() for source, aligner in self._aligners.items()}

    def report(self) -> DriftReport:
        metrics = self.metrics()
        if not metrics:
            return DriftReport(0.0, {}, 0.0, 0.0, True)
        duration = max(item.source_elapsed_seconds for item in metrics.values())
        max_seconds = max(
            (
                max(
                    abs(item.source_drift_seconds),
                    abs(item.monotonic_drift_seconds),
                    abs(item.corrected_drift_seconds),
                )
                for item in metrics.values()
            ),
            default=0.0,
        )
        max_ppm = max((abs(item.drift_ppm) for item in metrics.values()), default=0.0)
        return DriftReport(
            duration_seconds=duration,
            per_source=metrics,
            max_abs_drift_seconds=max_seconds,
            max_abs_drift_ppm=max_ppm,
            within_target=max_seconds <= self.drift_target_seconds,
        )


def _resample_int16(
    pcm: bytes,
    source_rate: int,
    target_rate: int,
    correction_ppm: float = 0.0,
) -> bytes:
    if source_rate < 1:
        raise ValueError("source sample rate must be positive")
    if not pcm:
        return b""
    if source_rate == target_rate and correction_ppm == 0:
        return pcm
    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 1:
        return samples.tobytes()
    effective_target = target_rate * (1.0 + correction_ppm / 1_000_000.0)
    output_count = max(1, int(round(samples.size * effective_target / source_rate)))
    source_positions = np.arange(samples.size, dtype=np.float64)
    target_positions = np.linspace(0, samples.size - 1, output_count)
    resampled = np.interp(target_positions, source_positions, samples.astype(np.float64))
    return np.clip(np.rint(resampled), -32768, 32767).astype(np.int16).tobytes()


def _ppm(elapsed: float, duration: float) -> float:
    if duration <= 0:
        return 0.0
    return (elapsed / duration - 1.0) * 1_000_000.0


def _validate_timestamp(value: Any, name: str) -> None:
    try:
        valid = math.isfinite(float(value)) and float(value) >= 0
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite non-negative number")
