"""Focused lifecycle tests for the Qt-facing live runtime."""

from __future__ import annotations

from types import SimpleNamespace

from core.live import runtime as runtime_module
from core.live.runtime import LiveRuntime, SessionState, SourceState


class _Signal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)


class _WorkerThatTracksShutdown:
    instances = []

    def __init__(self, *_args, **_kwargs) -> None:
        self.ready = _Signal()
        self.error = _Signal()
        self.started = 0
        self.cancelled = 0
        self.wait_timeouts = []
        type(self).instances.append(self)

    def start(self) -> None:
        self.started += 1

    def cancel(self) -> None:
        self.cancelled += 1

    def wait(self, timeout: int) -> bool:
        self.wait_timeouts.append(timeout)
        return True


class _SourceThatCannotStart:
    instances = []

    def __init__(self, *_args, **_kwargs) -> None:
        self.level_changed = _Signal()
        self.error_occurred = _Signal()
        self.recorder = SimpleNamespace(_output_path=None)
        self.cancelled = 0
        type(self).instances.append(self)

    def start(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled += 1


class _PipelineStub:
    def __init__(self, *_args, **_kwargs) -> None:
        self.scheduler = SimpleNamespace(max_pending_per_source=None)


def test_failed_source_start_cancels_and_waits_for_worker(monkeypatch):
    _WorkerThatTracksShutdown.instances.clear()
    _SourceThatCannotStart.instances.clear()
    monkeypatch.setattr(
        runtime_module, "PersistentWhisperWorker", _WorkerThatTracksShutdown
    )
    monkeypatch.setattr(runtime_module, "MicSource", _SourceThatCannotStart)
    monkeypatch.setattr(runtime_module, "LiveSessionPipeline", _PipelineStub)

    runtime = LiveRuntime()

    assert runtime.start(
        use_mic=True,
        use_system=False,
        model_name="tiny",
    ) is False

    worker = _WorkerThatTracksShutdown.instances[-1]
    source = _SourceThatCannotStart.instances[-1]
    assert worker.started == 1
    assert worker.cancelled == 1
    assert worker.wait_timeouts == [3000]
    assert source.cancelled == 1
    assert runtime.session_state is SessionState.FAILED
    assert runtime._states == {"mic": SourceState.FAILED}


def test_shutdown_cleans_worker_owned_by_failed_session():
    runtime = LiveRuntime()
    worker = _WorkerThatTracksShutdown()
    source = _SourceThatCannotStart()
    runtime._worker = worker
    runtime._sources = {"mic": source}
    runtime._states = {"mic": SourceState.FAILED}
    runtime._set_session_state(SessionState.FAILED)

    runtime.shutdown()

    assert worker.cancelled == 1
    assert worker.wait_timeouts == [3000]
    assert source.cancelled == 1


class _WorkerThatNeverStops:
    def __init__(self) -> None:
        self.shutdown_calls = 0
        self.wait_timeouts = []

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def wait(self, timeout: int) -> bool:
        self.wait_timeouts.append(timeout)
        return False  # simulates a worker stuck past the deadline


def test_finish_session_fails_instead_of_completing_when_worker_wont_stop():
    """A worker that ignores its shutdown deadline must not be treated as a
    clean finish — the pipeline could still be mutating in-flight segments,
    so reporting COMPLETED would hand the UI a silently truncated result."""
    runtime = LiveRuntime()
    worker = _WorkerThatNeverStops()
    runtime._worker = worker
    runtime._sources = {}
    runtime._states = {"mic": SourceState.RUNNING}
    runtime._pipeline = _PipelineStub()
    runtime._pipeline.flush = lambda: None
    runtime._pipeline.scheduler.stats = lambda: SimpleNamespace(queued=0, in_flight=0)
    runtime._pipeline.build_result = lambda *_a, **_kw: object()

    finished_calls = []
    errors = []
    runtime.finished.connect(lambda *args: finished_calls.append(args))
    runtime.error_occurred.connect(lambda *args: errors.append(args))

    runtime._finish_session()

    assert worker.shutdown_calls == 1
    assert worker.wait_timeouts == [3000]
    assert finished_calls == []
    assert errors == [("asr", "ASR worker did not stop in time")]
    assert runtime.session_state is SessionState.FAILED
    assert runtime._states == {"mic": SourceState.STOPPED}


def test_stop_does_not_start_a_second_finalizer(monkeypatch):
    runtime = LiveRuntime()
    runtime._states = {"mic": SourceState.RUNNING}
    runtime._set_session_state(SessionState.RUNNING)
    threads = []

    class _ThreadStub:
        def __init__(self, *, target, daemon) -> None:
            self.target = target
            self.daemon = daemon

        def start(self) -> None:
            threads.append(self)

    monkeypatch.setattr(
        runtime_module, "threading", SimpleNamespace(Thread=_ThreadStub)
    )

    runtime.stop()
    runtime.stop()

    assert runtime.session_state is SessionState.FINALIZING
    assert len(threads) == 1
