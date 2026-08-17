"""UI-independent orchestration for the live transcription primitives."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Callable

from core.live.asr_scheduler import DualQueueASRScheduler
from core.live.clock_aligner import DualSourceClockAligner
from core.live.contracts import AudioFrame, BufferedSpeechTurn, SegmentUpdate
from core.live.echo_detector import EchoDuplicateDetector
from core.live.overlap_timeline import LiveOverlapTimeline
from core.live.reconciler import LiveSegmentReconciler
from core.live.vad import PerSourceVAD, VADConfig


class LiveSessionPipeline:
    """Connect source frames to VAD, one ASR worker and a final timeline.

    The class owns no device and no UI. Sources feed timestamped frames from
    their existing bounded rings; emitted updates are safe to forward through
    a Qt signal. All transcript timestamps are relative to the first frame in
    the session, while clock metrics retain the original device timestamps.
    """

    def __init__(
        self,
        worker: Any,
        *,
        sources: tuple[str, ...] = ("mic", "system"),
        partial_interval_seconds: float = 2.0,
        vad_config: VADConfig | None = None,
        update_callback: Callable[[SegmentUpdate], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not sources or any(not source for source in sources):
            raise ValueError("sources must contain non-empty source IDs")
        if partial_interval_seconds <= 0:
            raise ValueError("partial_interval_seconds must be positive")
        self.sources = tuple(dict.fromkeys(sources))
        self.partial_interval_seconds = partial_interval_seconds
        self.update_callback = update_callback
        self._clock = clock
        self._session_origin: float | None = None
        self._aligner = DualSourceClockAligner()
        self._vads = {
            source: PerSourceVAD(source, config=vad_config) for source in self.sources
        }
        self._reconcilers = {
            source: LiveSegmentReconciler(source) for source in self.sources
        }
        self.timeline = LiveOverlapTimeline(detector=EchoDuplicateDetector())
        self.scheduler = DualQueueASRScheduler(
            worker,
            max_in_flight=1,
            result_callback=self._on_asr_result,
            clock=clock,
        )
        self._last_partial_end: dict[str, float] = {}

    def feed(self, frame: AudioFrame) -> tuple[BufferedSpeechTurn, ...]:
        """Process one source frame and enqueue due partial/final ASR work."""
        if frame.source not in self._vads:
            raise ValueError(f"unknown live source: {frame.source!r}")
        aligned = self._aligner.process(frame)
        relative = self._relative_frame(aligned)
        vad = self._vads[relative.source]
        completed = vad.feed(relative)
        submitted: list[BufferedSpeechTurn] = []

        for turn in completed:
            buffered = vad.pop_buffered(turn)
            if self.scheduler.submit(buffered.turn, buffered.pcm, final=True):
                submitted.append(buffered)
            self._last_partial_end.pop(relative.source, None)

        snapshot = vad.snapshot()
        if snapshot is not None and self._partial_due(snapshot):
            if self.scheduler.submit(snapshot.turn, snapshot.pcm, final=False):
                submitted.append(snapshot)
                self._last_partial_end[relative.source] = snapshot.turn.end
        return tuple(submitted)

    def flush(self, source: str | None = None) -> tuple[BufferedSpeechTurn, ...]:
        """Finalize buffered speech for one source or the whole session."""
        targets = (source,) if source is not None else self.sources
        submitted: list[BufferedSpeechTurn] = []
        for source_id in targets:
            try:
                vad = self._vads[source_id]
            except KeyError as exc:
                raise ValueError(f"unknown live source: {source_id!r}") from exc
            for turn in vad.flush():
                buffered = vad.pop_buffered(turn)
                if self.scheduler.submit(buffered.turn, buffered.pcm, final=True):
                    submitted.append(buffered)
            self._last_partial_end.pop(source_id, None)
        return tuple(submitted)

    def drift_report(self):
        return self._aligner.report()

    def final_segments(self) -> tuple[Any, ...]:
        return self.timeline.final_segments()

    def build_result(self, language: str = "auto") -> Any:
        """Return the ordinary batch model consumed by Library/Export/Content."""
        from domain.transcription import TranscriptionResult

        segments = list(self.final_segments())
        duration = max((float(segment.end) for segment in segments), default=0.0)
        labels = self.timeline.labels.as_dict()
        return TranscriptionResult(
            segments=segments,
            language=language,
            duration=duration,
            speaker_names={source: labels[source] for source in self.sources if source in labels},
        )

    def _relative_frame(self, frame: AudioFrame) -> AudioFrame:
        if self._session_origin is None:
            self._session_origin = frame.monotonic_timestamp
        timestamp = max(0.0, frame.monotonic_timestamp - self._session_origin)
        return replace(frame, monotonic_timestamp=timestamp)

    def _partial_due(self, snapshot: BufferedSpeechTurn) -> bool:
        previous = self._last_partial_end.get(snapshot.turn.source)
        if previous is None:
            return (
                snapshot.turn.end - snapshot.turn.start + 1e-9
                >= self.partial_interval_seconds
            )
        return snapshot.turn.end - previous + 1e-9 >= self.partial_interval_seconds

    def _on_asr_result(self, result: Any) -> None:
        source = result.turn.source
        reconciler = self._reconcilers[source]
        updates = reconciler.accept(
            result.turn,
            result.segments,
            final=bool(getattr(result, "final", True)),
        )
        for update in updates:
            if self.timeline.accept(update) and self.update_callback is not None:
                self.update_callback(update)
