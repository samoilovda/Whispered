"""Vertical mic/dual-source orchestration tests for the pre-L15 gate."""

from __future__ import annotations

from array import array
from dataclasses import dataclass

from core.live import AudioFrame, LiveSessionPipeline, SegmentState
from core.live.asr_worker import LiveASRResult
from core.live.vad import VADConfig


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, value):
        for slot in tuple(self._slots):
            slot(value)


@dataclass
class _Segment:
    start: float
    end: float
    text: str
    speaker: str
    words: list


class _Worker:
    def __init__(self):
        self.final = _Signal()
        self.partial = _Signal()
        self.decode_error = _Signal()
        self.calls = []
        self._next = 1

    def submit(self, turn, pcm, *, final=True):
        request = self._next
        self._next += 1
        self.calls.append((request, turn, pcm, final))
        return request

    def complete(self, index=0, text="hello"):
        request, turn, _pcm, final = self.calls[index]
        result = LiveASRResult(
            request,
            turn,
            (_Segment(turn.start, turn.end, text, turn.source, []),),
            0.1,
            final,
        )
        (self.final if final else self.partial).emit(result)


def _frame(sequence, timestamp, amplitude):
    return AudioFrame(
        "mic", sequence, timestamp, 100.0 + timestamp, 16_000,
        array("h", [amplitude] * 1_600).tobytes(),
    )


def test_frames_produce_relative_partial_then_immutable_final_timeline():
    worker = _Worker()
    updates = []
    pipeline = LiveSessionPipeline(
        worker,
        sources=("mic",),
        partial_interval_seconds=0.2,
        vad_config=VADConfig(end_silence_seconds=0.2),
        update_callback=updates.append,
    )

    pipeline.feed(_frame(0, 0.0, 8_000))
    pipeline.feed(_frame(1, 0.1, 8_000))
    assert worker.calls[0][3] is False
    worker.complete(0, "hel")
    assert updates[-1].state is SegmentState.PARTIAL
    assert updates[-1].segment.start == 0.0

    pipeline.feed(_frame(2, 0.2, 0))
    pipeline.feed(_frame(3, 0.3, 0))
    assert worker.calls[-1][3] is True
    worker.complete(len(worker.calls) - 1, "hello")
    assert pipeline.final_segments()[0].text == "hello"
    assert updates[-1].state is SegmentState.FINAL
    result = pipeline.build_result("en")
    assert result.segments[0].text == "hello"
    assert result.speaker_names == {"mic": "Microphone"}
