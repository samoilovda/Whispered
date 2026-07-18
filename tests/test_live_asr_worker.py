"""Unit tests for L6 live ASR protocol; no model or subprocess required."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import pytest

from core.live import SpeechTurn
from core.live.asr_worker import (
    LiveASRResult,
    PersistentWhisperWorker,
    _LiveDecodeEngine,
    _percentile,
    _serve_live_commands,
)


@dataclass
class _RawSegment:
    t0: int
    t1: int
    text: str


class _FakeModel:
    def __init__(self, *, block=False):
        self.calls = []
        self.block = block

    def auto_detect_language(self, audio, n_threads):
        self.calls.append(("detect", len(audio), n_threads))
        return (("ru", 0.99), {})

    def transcribe(self, audio, **params):
        self.calls.append(("transcribe", len(audio), params["language"]))
        if self.block:
            while not params["abort_callback"]():
                time.sleep(0.005)
            return []
        return [_RawSegment(10, 90, " hello ")]


def _turn():
    return SpeechTurn("mic", 10.0, 11.0, 4, 13)


def test_decode_loads_language_once_and_offsets_batch_segments():
    model = _FakeModel()
    abort = threading.Event()
    engine = _LiveDecodeEngine(model, "auto", 3, abort)

    first = engine.decode(1, _turn(), b"\x00\x00" * 1600)
    second = engine.decode(2, _turn(), b"\x00\x00" * 1600)

    assert first["segments"][0] == {
        "start": 10.1,
        "end": 10.9,
        "text": " hello ",
        "speaker": "mic",
    }
    assert second["segments"] == first["segments"]
    assert [call[0] for call in model.calls] == ["detect", "transcribe", "transcribe"]


def test_command_server_keeps_one_model_engine_and_returns_finals():
    commands: queue.Queue = queue.Queue()
    results: queue.Queue = queue.Queue()
    model = _FakeModel()
    engine = _LiveDecodeEngine(model, "ru", 2, threading.Event())
    thread = threading.Thread(
        target=_serve_live_commands,
        args=(commands, results, engine, threading.Event()),
        daemon=True,
    )
    thread.start()
    commands.put(("transcribe", 1, _turn(), b"\x00\x00" * 800))
    commands.put(("transcribe", 2, _turn(), b"\x00\x00" * 800))
    commands.put(("shutdown",))
    thread.join(timeout=1.0)

    messages = [results.get_nowait(), results.get_nowait()]
    assert [message[0] for message in messages] == ["final", "final"]
    assert len([call for call in model.calls if call[0] == "transcribe"]) == 2
    assert not thread.is_alive()


def test_abort_callback_stops_inflight_decode():
    commands: queue.Queue = queue.Queue()
    results: queue.Queue = queue.Queue()
    abort = threading.Event()
    engine = _LiveDecodeEngine(_FakeModel(block=True), "ru", 2, abort)
    thread = threading.Thread(
        target=_serve_live_commands,
        args=(commands, results, engine, abort),
        daemon=True,
    )
    thread.start()
    commands.put(("transcribe", 1, _turn(), b"\x00\x00" * 800))
    time.sleep(0.02)
    abort.set()
    thread.join(timeout=0.5)

    assert not thread.is_alive()


def test_worker_metrics_and_configuration_are_bounded():
    worker = PersistentWhisperWorker("tiny", n_threads=2, queue_size=1)
    assert worker.metrics().final_latency_p95 is None
    assert _percentile([1.0, 3.0, 2.0], 0.95) == 3.0
    assert _percentile([], 0.95) is None
    assert worker.submit(_turn(), b"pcm") == 1
    assert worker.submit(_turn(), b"pcm") is None
    assert worker.metrics().submitted == 1
    worker.cancel()
    assert worker.metrics().cancelled is True


def test_live_result_is_a_stable_envelope():
    result = LiveASRResult(1, _turn(), (), 0.25)
    assert result.request_id == 1
    assert result.turn.source == "mic"
    assert result.latency_seconds == pytest.approx(0.25)


def test_partial_command_preserves_non_terminal_result_marker():
    commands: queue.Queue = queue.Queue()
    results: queue.Queue = queue.Queue()
    engine = _LiveDecodeEngine(_FakeModel(), "ru", 2, threading.Event())
    commands.put(("transcribe", 1, _turn(), b"\x00\x00" * 800, False))
    commands.put(("shutdown",))
    _serve_live_commands(commands, results, engine, threading.Event())

    kind, payload = results.get_nowait()
    assert kind == "decoded"
    assert payload["final"] is False
