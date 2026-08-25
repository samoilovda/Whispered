"""Real-Qt regressions for WorkerRegistry's cancel/retain/dispose mechanics.

Used to also cover this through InsightsPanel/YouTubePanel directly (each
owned a WorkerRegistry and a queue of InsightsWorkers) — B5c and B5d (see
docs/UI_REDESIGN_PLAN_2026-09.ru.md) moved that ownership to MainWindow's
own _insights_job/_youtube_job (application/steps.py's steps run via
JobRunner), so neither panel creates or registers a worker anymore. The
mechanics themselves are still exercised below, directly against
WorkerRegistry, without needing any panel at all.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QThread, pyqtSignal as _pyqtSignal


class _SimpleWorker(QThread):
    """Minimal worker for WorkerRegistry tests."""

    done = _pyqtSignal(str)
    error_occurred = _pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._release = threading.Event()
        self.cancelled = False

    def run(self) -> None:
        self._release.wait(5)

    def cancel(self) -> None:
        self.cancelled = True
        self._release.set()


def test_worker_registry_register_and_retire(process_events):
    """register() stores worker; retire() cancels and disconnects signals."""
    from core.worker_registry import WorkerRegistry

    registry = WorkerRegistry()
    results: list[str] = []
    w = _SimpleWorker()
    w.done.connect(lambda v: results.append(v))
    registry.register(w, name="test")
    w.start()

    # Worker is active
    assert "test" in registry.active_names

    # retire: cancel, disconnect, move to retired
    # cancel() also calls _release.set(), so the thread will stop quickly
    registry.retire(w)
    process_events()

    assert "test" not in registry.active_names
    assert w.cancelled is True

    # Give the thread a moment to finish and let Qt process deleteLater
    import time
    deadline = time.monotonic() + 2.0
    while registry.retired_count > 0 and time.monotonic() < deadline:
        process_events()
        time.sleep(0.02)

    # After thread finished, retired dict should be empty (deleteLater called)
    assert registry.retired_count == 0

    # Late signal after disconnect must not reach results list
    # (can't emit on deleted C++ object, so only check if still alive)
    try:
        w.done.emit("late")
        process_events()
        assert results == []
    except RuntimeError:
        pass  # object already deleted — that's fine


class _HungWorker(QThread):
    """Worker that never releases; cancel() is a no-op (simulates hung thread)."""

    done = _pyqtSignal()
    error_occurred = _pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._release = threading.Event()
        self.cancelled = False

    def run(self) -> None:
        # Ignore cancel; only _release.set() from the test unblocks it
        self._release.wait(10)

    def cancel(self) -> None:
        # Deliberately do NOT set _release — simulates a hung worker
        self.cancelled = True


def test_worker_registry_shutdown_all_bounded(process_events):
    """shutdown_all() with short timeout returns unfinished names, no crash."""
    from core.worker_registry import WorkerRegistry

    registry = WorkerRegistry()
    w = _HungWorker()
    registry.register(w, name="hung")
    w.start()
    assert w.isRunning()

    unfinished = registry.shutdown_all(timeout_ms=200)
    # Thread did not stop in 200 ms — should appear in unfinished list
    assert len(unfinished) >= 1

    # Cleanup — release the thread so it can exit cleanly.
    # Catch RuntimeError in case Qt has already deleted the C++ object.
    w._release.set()
    try:
        import time
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                if not w.isRunning():
                    break
            except RuntimeError:
                break  # C++ object gone — thread is done
            process_events()
            time.sleep(0.05)
    except RuntimeError:
        pass  # Already cleaned up



def test_base_worker_emit_terminal_deduplication(process_events):
    """_emit_terminal suppresses the second call in the same run."""
    from core.base_worker import BaseWorker

    class _DualEmitter(BaseWorker):
        done = _pyqtSignal()
        error_occurred = _pyqtSignal(str)

        def _on_error(self, msg: str) -> None:
            self.error_occurred.emit(msg)

        def _execute(self) -> None:
            # First emit — should succeed
            self._emit_terminal(self.done)
            # Second emit — should be suppressed
            self._emit_terminal(self.done)

    emit_count = [0]
    w = _DualEmitter()
    w.done.connect(lambda: emit_count.__setitem__(0, emit_count[0] + 1))
    w.start()
    QThread.wait(w, 2000)
    process_events()

    assert emit_count[0] == 1, "duplicate terminal signal was not suppressed"
    w.deleteLater()
    process_events()
