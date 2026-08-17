"""
Whispered – WorkerRegistry
Centralised lifecycle manager for all BaseWorker/QThread objects.

Replaces per-panel retired_workers dicts and unbounded wait() calls with a
single, consistent pattern:

    registry.register(worker, name="transcriber")
    # … worker does its work …
    registry.retire(worker)          # disconnect business signals, cancel

On window close:
    unfinished = registry.shutdown_all(timeout_ms=5000)
    if unfinished:
        logger.error("threads still running: %s", unfinished)

Rules:
- No QThread.terminate() under any circumstances.
- Bounded waits everywhere; the caller supplies the deadline.
- Retired workers are kept alive (strong reference) until QThread.finished
  fires, then deleteLater() is called and the reference is dropped.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QThread, pyqtSlot

from core.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class WorkerRegistry(QObject):
    """Centralised lifecycle tracker for BaseWorker / QThread objects.

    Typical panel usage::

        def __init__(self):
            self._registry = WorkerRegistry(parent=self)

        def _start(self):
            w = MyWorker()
            w.finished.connect(self._on_result)
            w.error_occurred.connect(self._on_error)
            self._registry.register(w, name="my_worker")
            w.start()

        def _cancel(self):
            self._registry.retire_all()

        def shutdown(self):
            self._registry.shutdown_all(timeout_ms=3000)
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # name → live worker (strong reference keeps C++ object alive)
        self._active: dict[str, QThread] = {}
        # id(worker) → worker  (retired, awaiting thread finish)
        self._retired: dict[int, QThread] = {}

    # ── Registration ─────────────────────────────────────────────────────

    def register(self, worker: QThread, *, name: str) -> None:
        """Take a strong reference to *worker* under *name*.

        If a worker is already registered under *name* it is silently
        retired first (the previous operation was replaced).
        """
        if name in self._active:
            logger.debug(
                "WorkerRegistry: replacing existing worker '%s'", name
            )
            self._retire_one(self._active.pop(name))
        self._active[name] = worker

    # ── Retirement ───────────────────────────────────────────────────────

    def retire(self, worker: QThread) -> None:
        """Disconnect business signals, cancel, and move to retired set.

        The worker is *not* removed from strong-ref storage until its
        thread actually finishes (QThread.finished).
        """
        name = next(
            (k for k, v in self._active.items() if v is worker), None
        )
        if name is not None:
            del self._active[name]
        self._retire_one(worker)

    def retire_all(self) -> None:
        """Retire every currently active worker."""
        workers = list(self._active.values())
        self._active.clear()
        for w in workers:
            self._retire_one(w)

    def _retire_one(self, worker: QThread) -> None:
        """Internal: disconnect business signals, cancel, track in retired."""
        if not worker:
            return

        _try_disconnect_business_signals(worker)

        if not worker.isRunning():
            worker.deleteLater()
            return

        worker_key = id(worker)
        self._retired[worker_key] = worker

        # Subscribe to QThread.finished (base class signal, not any shadow).
        QThread.finished.__get__(worker, type(worker)).connect(
            self._on_retired_finished
        )

        # Race guard: thread may have finished between isRunning() and connect.
        if not worker.isRunning():
            self._dispose_retired(worker_key)
            return

        _try_cancel(worker)

    @pyqtSlot()
    def _on_retired_finished(self) -> None:
        sender = self.sender()
        if sender is not None:
            self._dispose_retired(id(sender))

    def _dispose_retired(self, worker_key: int) -> None:
        worker = self._retired.pop(worker_key, None)
        if worker is not None:
            worker.deleteLater()

    # ── Bounded shutdown ─────────────────────────────────────────────────

    def shutdown_all(self, timeout_ms: int = 5000) -> list[str]:
        """Cancel all active and retired workers; wait up to *timeout_ms* total.

        Returns the names/ids of threads that did not finish in time.
        A warning is logged for each; no terminate() is ever called.

        The timeout is a *global* deadline across all workers, not per-worker.
        """
        self.retire_all()

        deadline = time.monotonic() + timeout_ms / 1000.0
        unfinished: list[str] = []

        for key, worker in list(self._retired.items()):
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remaining_ms == 0:
                unfinished.append(f"id={key} ({type(worker).__name__})")
                continue
            if worker.isRunning():
                worker.wait(remaining_ms)
            if worker.isRunning():
                unfinished.append(f"id={key} ({type(worker).__name__})")
            else:
                self._dispose_retired(key)

        if unfinished:
            logger.error(
                "WorkerRegistry.shutdown_all: threads still running after "
                "%d ms: %s",
                timeout_ms,
                unfinished,
            )
        return unfinished

    # ── Diagnostics ──────────────────────────────────────────────────────

    @property
    def active_names(self) -> list[str]:
        return list(self._active.keys())

    @property
    def retired_count(self) -> int:
        return len(self._retired)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _try_cancel(worker: QThread) -> None:
    """Call worker.cancel() if it exists (BaseWorker contract)."""
    cancel = getattr(worker, "cancel", None)
    if callable(cancel):
        try:
            cancel()
        except Exception:
            logger.exception("WorkerRegistry: cancel() raised")


def _try_disconnect_business_signals(worker: QThread) -> None:
    """Disconnect business-level signals declared on the worker class.

    BaseWorker subclasses may implement ``_disconnect_business_signals()``
    for fine-grained control.  If not present, we fall back to
    disconnecting every signal listed in the class's ``__dict__`` that is
    a pyqtSignal descriptor (identified by having a ``disconnect`` method
    on the bound instance attribute).

    We deliberately *do not* disconnect QThread's built-in ``finished``
    signal — that is needed for the retirement cleanup path.
    """
    disconnect_fn = getattr(worker, "_disconnect_business_signals", None)
    if callable(disconnect_fn):
        try:
            disconnect_fn()
        except Exception:
            logger.exception(
                "WorkerRegistry: _disconnect_business_signals() raised"
            )
        return

    # Fallback: disconnect any bound signal attribute that has a disconnect
    # method, skipping the base QThread built-ins.
    _QTHREAD_BUILTINS = frozenset(
        {"finished", "started", "terminated", "objectNameChanged"}
    )
    for name in type(worker).__dict__:
        if name in _QTHREAD_BUILTINS:
            continue
        bound = getattr(worker, name, None)
        if bound is None:
            continue
        disconnect_meth = getattr(bound, "disconnect", None)
        if callable(disconnect_meth):
            try:
                disconnect_meth()
            except (RuntimeError, TypeError):
                pass  # already disconnected or C++ object gone
