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


@pytest.mark.parametrize("panel_kind", ["youtube", "insights"])
def test_running_worker_is_retained_until_qthread_finished(
    panel_kind, process_events
):
    if panel_kind == "youtube":
        from ui.youtube_panel import YouTubePanel

        panel = YouTubePanel()
    else:
        from ui.insights_panel import InsightsPanel

        panel = InsightsPanel()

    delete_calls: list[object] = []
    worker = _SlowWorker(delete_calls, parent=panel)
    worker.finished.connect(panel._on_finished)
    worker.error_occurred.connect(panel._on_error)
    panel._workers["chapters"] = worker
    panel._pending = 1

    worker.start()
    assert worker.started_event.wait(1)
    try:
        panel.clear()

        assert worker.cancelled is True
        assert worker.wait_timeouts == [2000]
        assert delete_calls == []
        assert panel._workers == {}
        assert panel._retired_workers[id(worker)] is worker

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

        assert panel._retired_workers == {}
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
