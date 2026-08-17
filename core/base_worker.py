"""
Whispered – Base QThread worker
Common cancel/error infrastructure shared by all background workers.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QThread

from core.logger import get_logger

logger = get_logger(__name__)


class BaseWorker(QThread):
    """QThread base class providing thread-safe cancellation and error safety.

    Subclasses implement ``_execute()`` with their business logic and
    ``_on_error(msg)`` to emit their specific error signal.  ``run()``
    wraps ``_execute()`` in a try/except so an unhandled exception is
    caught and forwarded rather than silently terminating the thread.

    Terminal-signal contract
    ------------------------
    Each concrete worker must emit **exactly one** terminal signal per run
    (either its success signal or an error signal).  Use ``_emit_terminal``
    to enforce this: subsequent calls are silently ignored, preventing
    double-emission from error paths that overlap with the happy path.

    Lifecycle / WorkerRegistry integration
    ---------------------------------------
    ``_disconnect_business_signals()`` is called by ``WorkerRegistry`` when
    retiring a worker.  The default implementation disconnects every
    pyqtSignal-like attribute (those with a ``disconnect`` method) except
    QThread's built-in signals.  Subclasses with complex signal topologies
    may override it for finer control.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = threading.Event()
        self._terminal_emitted = threading.Event()

    # ── cancellation ────────────────────────────────────────────────────

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    # ── terminal-signal guard ────────────────────────────────────────────

    def _emit_terminal(self, signal, *args) -> bool:
        """Emit *signal* with *args* if no terminal signal has been emitted yet.

        Returns ``True`` if the signal was emitted, ``False`` if it was
        suppressed because a terminal signal was already sent for this run.
        """
        if self._terminal_emitted.is_set():
            logger.warning(
                "%s._emit_terminal: suppressed duplicate terminal signal",
                type(self).__name__,
            )
            return False
        self._terminal_emitted.set()
        signal.emit(*args)
        return True

    # ── WorkerRegistry hook ──────────────────────────────────────────────

    def _disconnect_business_signals(self) -> None:
        """Disconnect all business-level signals.

        Called by WorkerRegistry when retiring this worker so that late
        emissions after the owner panel has moved on cannot corrupt state.

        The default implementation disconnects every attribute whose bound
        value exposes a ``disconnect()`` method, **excluding** QThread's
        own built-in signals (``finished``, ``started``, ``terminated``,
        ``objectNameChanged``) which are managed by the registry itself.

        Subclasses may override to be more selective.
        """
        _QTHREAD_BUILTINS = frozenset(
            {"finished", "started", "terminated", "objectNameChanged"}
        )
        for name in type(self).__dict__:
            if name in _QTHREAD_BUILTINS:
                continue
            bound = getattr(self, name, None)
            if bound is None:
                continue
            disconnect_meth = getattr(bound, "disconnect", None)
            if callable(disconnect_meth):
                try:
                    disconnect_meth()
                except (RuntimeError, TypeError):
                    pass  # already disconnected or C++ object gone

    # ── execution template ───────────────────────────────────────────────

    def run(self) -> None:
        self._terminal_emitted.clear()
        try:
            self._execute()
        except Exception as exc:
            logger.exception("%s unhandled error", type(self).__name__)
            self._on_error(str(exc))

    def _execute(self) -> None:
        raise NotImplementedError

    def _on_error(self, msg: str) -> None:
        """Emit the worker-specific error signal. Subclasses must override."""
