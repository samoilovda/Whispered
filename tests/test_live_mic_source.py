"""Unit tests for the L3 microphone adapter; no real audio device required."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

from core.live import MicSource


class _FakeRecorder(QObject):
    level_changed = pyqtSignal(float)
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None, *, frame_sink=None):
        super().__init__(parent)
        self.frame_sink = frame_sink
        self.started_with = None
        self.calls: list[str] = []
        self._recording = False
        self._path = "/tmp/fake-recording.wav"

    def start(self, device=None):
        self.started_with = device
        self.calls.append("start")
        self._recording = True

    def pause(self):
        self.calls.append("pause")

    def resume(self):
        self.calls.append("resume")

    def stop(self):
        self.calls.append("stop")
        self._recording = False
        return self._path

    def is_recording(self):
        return self._recording

    @property
    def elapsed_seconds(self):
        return 1.25

    def emit_frame(self, pcm=b"pcm", time_info=None):
        assert self.frame_sink is not None
        self.frame_sink(pcm, 1, time_info)


@dataclass
class _Factory:
    instances: list[_FakeRecorder]

    def __call__(self, parent=None, *, frame_sink=None):
        recorder = _FakeRecorder(parent, frame_sink=frame_sink)
        self.instances.append(recorder)
        return recorder


def test_disabled_feature_does_not_install_frame_sink_or_start():
    factory = _Factory([])
    source = MicSource(enabled=False, recorder_factory=factory)

    assert factory.instances[0].frame_sink is None
    assert source.start() is False
    assert factory.instances[0].calls == []


def test_enabled_adapter_preserves_recorder_controls_and_exposes_frames():
    factory = _Factory([])
    source = MicSource(
        enabled=True,
        device=7,
        capacity_frames=4,
        capacity_bytes=32,
        recorder_factory=factory,
    )
    recorder = factory.instances[0]
    levels: list[float] = []
    source.level_changed.connect(levels.append)

    assert source.start() is True
    assert recorder.started_with == 7
    recorder.level_changed.emit(0.4)
    recorder.emit_frame(b"pcm", {"inputBufferAdcTime": 12.5})

    frame = source.get_frame(timeout=0)
    assert frame is not None
    assert (frame.source, frame.sequence, frame.source_timestamp) == ("mic", 0, 12.5)
    assert frame.pcm == b"pcm"
    assert levels == [0.4]

    source.pause()
    source.resume()
    assert source.stop() == "/tmp/fake-recording.wav"
    assert recorder.calls == ["start", "pause", "resume", "stop"]
    assert source.is_recording() is False
    assert source.get_frame(timeout=0) is None


def test_adapter_cancel_discards_pending_frames():
    factory = _Factory([])
    source = MicSource(enabled=True, recorder_factory=factory)
    recorder = factory.instances[0]
    source.start()
    recorder.emit_frame()

    assert source.cancel() == "/tmp/fake-recording.wav"
    assert source.get_frame(timeout=0) is None
    assert source.stats().cancelled is True


def test_legacy_recorder_callback_keeps_wav_queue_and_optional_sink():
    numpy = pytest.importorskip("numpy")
    from core.recorder import Recorder

    legacy = Recorder()
    block = numpy.array([[100], [-100]], dtype=numpy.int16)
    legacy._audio_callback(block, 2, None, None)
    assert legacy._frame_sink is None
    assert legacy._q.get_nowait() == bytes(block)

    captured: list[tuple[bytes, int, object]] = []
    live = Recorder(frame_sink=lambda pcm, frames, info: captured.append((pcm, frames, info)))
    info = {"inputBufferAdcTime": 4.0}
    live._audio_callback(block, 2, info, None)
    assert captured == [(bytes(block), 2, info)]
    assert live._q.get_nowait() == bytes(block)
