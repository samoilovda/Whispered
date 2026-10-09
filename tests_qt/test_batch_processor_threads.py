"""Real-Qt regressions for batch transcription (batch_processor.py).

tests/test_batch_processor.py runs on Qt stubs where QThread.start() is a
no-op, so it never saw that BatchWorker waited forever: the transcription
worker's signals were auto-connected to plain callables from a thread with
no event loop, and such calls are queued to that thread and never run.
Cancelling then blocked the GUI thread in an unbounded wait().
"""

from __future__ import annotations

import threading
import time

import pytest
from PyQt6.QtCore import pyqtSignal

import transcriber
from batch_processor import BatchProcessor, BatchStatus
from core.base_worker import BaseWorker


class _FakeTranscriptionWorker(BaseWorker):
    """Stands in for TranscriptionWorker: no child process, no model."""

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    # Set by a test to keep the "transcription" running until cancelled.
    hold = False

    def __init__(self, **kwargs):
        super().__init__()
        self.filepath = kwargs["filepath"]

    def _disconnect_business_signals(self) -> None:
        for signal in (self.progress, self.finished, self.error):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass

    def _execute(self):
        self.progress.emit(50, "halfway")
        while type(self).hold and not self.is_cancelled():
            time.sleep(0.01)
        if self.is_cancelled():
            time.sleep(0.2)          # the child process takes a moment to die
            self.error.emit("Cancelled")
            return
        self.finished.emit(f"result of {self.filepath}")


@pytest.fixture
def fake_worker(monkeypatch):
    monkeypatch.setattr(transcriber, "TranscriptionWorker", _FakeTranscriptionWorker)
    _FakeTranscriptionWorker.hold = False
    yield _FakeTranscriptionWorker
    _FakeTranscriptionWorker.hold = False


@pytest.fixture
def media(tmp_path):
    paths = []
    for name in ("a.wav", "b.wav"):
        path = tmp_path / name
        path.write_bytes(b"fake audio")
        paths.append(str(path))
    return paths


def _wait_until(process_events, predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process_events()
        if predicate():
            return True
    return False


def test_batch_completes_every_item(fake_worker, media, process_events):
    processor = BatchProcessor()
    processor.add_files(media)
    finished: list[tuple[int, object]] = []
    done = threading.Event()
    processor.item_finished.connect(lambda i, r: finished.append((i, r)))
    processor.batch_finished.connect(done.set)

    processor.start(model_name="tiny")

    assert _wait_until(process_events, done.is_set), [i.status for i in processor.items]
    assert [i.status for i in processor.items] == [BatchStatus.COMPLETE] * 2
    assert finished == [(0, f"result of {media[0]}"), (1, f"result of {media[1]}")]
    assert processor.items[0].progress == 100
    processor.shutdown()


def test_cancel_mid_item_returns_at_once_and_ends_the_batch(fake_worker, media, process_events):
    fake_worker.hold = True
    processor = BatchProcessor()
    processor.add_files(media)
    done = threading.Event()
    processor.batch_finished.connect(done.set)
    processor.start(model_name="tiny")
    assert _wait_until(
        process_events, lambda: processor.items[0].status == BatchStatus.PROCESSING)

    started = time.monotonic()
    processor.cancel()
    assert time.monotonic() - started < 0.5      # never blocks the GUI thread

    assert _wait_until(process_events, done.is_set)
    assert [i.status for i in processor.items] == [BatchStatus.CANCELLED] * 2
    assert not processor.is_processing
    processor.shutdown()


def test_shutdown_mid_item_is_bounded(fake_worker, media, process_events):
    fake_worker.hold = True
    processor = BatchProcessor()
    processor.add_files(media)
    processor.start(model_name="tiny")
    assert _wait_until(
        process_events, lambda: processor.items[0].status == BatchStatus.PROCESSING)

    started = time.monotonic()
    processor.shutdown(timeout_ms=3000)
    assert time.monotonic() - started < 3.5
    assert not processor.is_processing
