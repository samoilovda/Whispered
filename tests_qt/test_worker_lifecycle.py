"""Real-Qt regressions for cancellation of long-running insight workers."""

from __future__ import annotations

import threading

import pytest
from PyQt6.QtCore import QThread, pyqtSignal


class _SlowWorker(QThread):
    """Network-free worker whose bounded ``wait`` deliberately times out."""

    finished = pyqtSignal(str, object)
    error_occurred = pyqtSignal(str, str)

    def __init__(self, delete_calls: list[object], parent=None):
        super().__init__(parent)
        self.started_event = threading.Event()
        self.release_event = threading.Event()
        self.cancelled = False
        self.wait_timeouts: list[int] = []
        self._delete_calls = delete_calls

    def run(self) -> None:
        # Queue a business result before cancellation.  Qt may still deliver
        # an already-posted queued call after disconnect(), so the panels
        # also have to reject results whose sender is no longer current.
        self.finished.emit("chapters", [])
        self.started_event.set()
        self.release_event.wait(5)

    def cancel(self) -> None:
        self.cancelled = True

    def wait(self, timeout: int = 0) -> bool:
        self.wait_timeouts.append(timeout)
        return False

    def deleteLater(self) -> None:  # noqa: N802 - Qt API spelling
        self._delete_calls.append(self)
        super().deleteLater()


@pytest.mark.parametrize("panel_kind", ["youtube"])
def test_running_worker_is_retained_until_qthread_finished(
    panel_kind, process_events
):
    # InsightsPanel used to be parametrized in here too, but B5c (see
    # docs/UI_REDESIGN_PLAN_2026-09.ru.md) moved its worker/registry
    # ownership to MainWindow's own _insights_job (application/steps.py's
    # "insights" step run via JobRunner) — the panel itself no longer
    # creates or registers a worker, so this specific WorkerRegistry
    # mechanics test no longer applies to it. YouTubePanel is unchanged
    # (still B5d) and keeps this coverage until its own migration.
    from ui.youtube_panel import YouTubePanel

    panel = YouTubePanel()

    delete_calls: list[object] = []
    worker = _SlowWorker(delete_calls, parent=panel)
    worker.finished.connect(panel._on_finished)
    worker.error_occurred.connect(panel._on_error)
    panel._workers["chapters"] = worker
    # Production code (_generate/_start_next_worker) registers each worker
    # with the panel's WorkerRegistry at creation time; this test injects
    # the worker directly to control its timing, so it has to do the same.
    panel._registry.register(worker, name="chapters")
    panel._pending = 1

    worker.start()
    assert worker.started_event.wait(1)
    try:
        panel.clear()

        assert worker.cancelled is True
        # WorkerRegistry.shutdown_all() computes the wait from a global
        # deadline (deadline - now()), not a fixed constant like the old
        # per-worker wait(timeout) call — allow for wall-clock jitter
        # between starting the deadline and reaching wait().
        assert len(worker.wait_timeouts) == 1
        assert 1900 <= worker.wait_timeouts[0] <= 2000
        assert delete_calls == []
        assert panel._workers == {}
        assert panel._registry.retired_count == 1

        # Neither the already-queued result nor signals emitted after
        # disconnect may decrement a replacement run's count.
        panel._pending = 7
        worker.finished.emit("chapters", [])
        worker.error_occurred.emit("chapters", "late")
        process_events()
        assert panel._pending == 7

        worker.release_event.set()
        assert QThread.wait(worker, 1000)
        process_events()

        assert panel._registry.retired_count == 0
        assert delete_calls == [worker]
    finally:
        # A failed assertion must never leave a real QThread running in the
        # test process (Qt aborts when such an object is destroyed).
        worker.release_event.set()
        try:
            if worker.isRunning():
                QThread.wait(worker, 1000)
        except RuntimeError:
            # The success path has already processed deleteLater().
            pass
        panel.close()
        process_events()


# ─── WorkerRegistry tests ─────────────────────────────────────────────────────

from PyQt6.QtCore import pyqtSignal as _pyqtSignal  # noqa: E402 (after class defs)


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
