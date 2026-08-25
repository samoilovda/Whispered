"""QThread wrapper around JobEngine.run() (see
docs/UI_REDESIGN_PLAN_2026-09.ru.md, B1).

Lives in ``core/`` rather than ``application/`` (the plan's own file list
said the latter) to match this codebase's actual convention: every other
QThread-based worker — ``core/base_worker.py``, ``core/insights_worker.py``,
``core/ai_worker.py``, ``core/chat_worker.py``, ``core/cover_worker.py``,
``core/book_batch_worker.py`` — lives there, and every module in
``application/`` so far (``document_session.py``, ``export_controller.py``,
``job_engine.py``, ``artifact_provenance.py``, ``steps.py``) is deliberately
Qt-free. ``JobRunner`` needs ``pyqtSignal``, so it belongs with the other
Qt workers, not as the first Qt import into ``application/``.

``JobEngine.run()`` is a single blocking call — CLAUDE.md's rule that
anything longer than ~100ms goes through a QThread applies to it exactly
as much as to transcription or an AI worker. ``JobRunner`` is that
QThread: ``_execute()`` calls ``JobEngine.run()`` once, and
``JobRun``'s ``on_step_started``/``on_outcome`` observer callbacks
(``application/job_engine.py``) are turned into Qt signals as steps
resolve, instead of the caller only learning anything once the whole run
has finished.

Construction is two-step on purpose: build the ``JobRunner`` first (spec
only), use its ``make_progress_callback(name)`` to give each step's
``StepContext`` a real ``on_progress`` wired to *this* instance's
``step_progress`` signal (via ``application.steps.build_runners``'s
``progress_factory``), then call ``set_runners()`` before ``start()``.
That ordering is the only way to route a step's progress into a signal
that doesn't exist until the QThread object itself does.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from PyQt6.QtCore import pyqtSignal

from application.job_engine import CacheCheck, JobEngine, JobRun, StepRunner
from core.base_worker import BaseWorker
from domain.job import JobSpec, StepOutcome


class JobRunner(BaseWorker):
    """Runs one JobSpec off the UI thread.

    Signals
    -------
    step_started(name)
        A step began resolving (about to cache-check, then possibly run).
    step_progress(name, percent, message)
        Forwarded from that step's own ``on_progress`` callback.
    step_finished(name, outcome)
        The step got a ``domain.job.StepOutcome`` — SUCCEEDED, FAILED,
        SKIPPED (cache hit), or CANCELLED.
    job_finished(run)
        The whole run has nothing left to resolve. Always emitted exactly
        once, whether the run completed cleanly or ``JobEngine.run()``
        itself raised (see ``_on_error`` below) — one terminal signal
        callers can depend on either way, matching every other worker in
        this codebase (``core/base_worker.py``).
    """

    step_started = pyqtSignal(str)
    step_progress = pyqtSignal(str, int, str)
    step_finished = pyqtSignal(str, object)   # StepOutcome
    job_finished = pyqtSignal(object)          # JobRun

    def __init__(
        self,
        spec: JobSpec,
        *,
        run_state: Optional[JobRun] = None,
        resource_limits: Optional[Dict[str, int]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._spec = spec
        self._engine = JobEngine(resource_limits)
        self._run_state = run_state if run_state is not None else JobRun(spec=spec)
        self._run_state.on_step_started = self._handle_step_started
        self._run_state.on_outcome = self._handle_outcome
        self._runners: Optional[Dict[str, StepRunner]] = None
        self._cache_checks: Optional[Dict[str, CacheCheck]] = None

    @property
    def run_state(self) -> JobRun:
        return self._run_state

    def set_runners(
        self,
        runners: Dict[str, StepRunner],
        cache_checks: Optional[Dict[str, CacheCheck]] = None,
    ) -> None:
        """Provide the step -> callable mapping to run. Must be called
        before ``start()`` — see the module docstring for why this isn't
        just a constructor argument."""
        self._runners = runners
        self._cache_checks = cache_checks

    def make_progress_callback(self, name: str) -> Callable[[int, str], None]:
        """A progress callback bound to *name*, for building that step's
        ``StepContext.on_progress`` (see ``application/steps.py``'s
        ``build_runners(..., progress_factory=...)``) before this
        runner's own ``runners`` dict is built and passed to
        ``set_runners()``."""

        def _emit(percent: int, message: str) -> None:
            self.step_progress.emit(name, percent, message)

        return _emit

    # ── cancellation ──────────────────────────────────────────────────

    def cancel(self) -> None:
        super().cancel()
        # JobRun.cancel() is the flag every step's runner (via
        # StepContext.is_cancelled, bound to this same JobRun — see
        # application/steps.py) and JobEngine itself actually check;
        # BaseWorker's own _cancelled flag alone wouldn't stop anything
        # already running inside JobEngine.run().
        self._run_state.cancel()

    # ── JobEngine observer callbacks ────────────────────────────────────
    # Called from JobEngine's own worker threads (it spawns one per
    # concurrently-ready step), not from this QThread's run() frame.
    # pyqtSignal.emit() is safe to call from any thread — Qt queues the
    # connection into the receiving object's thread automatically.

    def _handle_step_started(self, name: str) -> None:
        self.step_started.emit(name)

    def _handle_outcome(self, outcome: StepOutcome) -> None:
        self.step_finished.emit(outcome.name, outcome)

    # ── execution ────────────────────────────────────────────────────

    def _execute(self) -> None:
        if self._runners is None:
            raise RuntimeError("JobRunner.set_runners() must be called before start()")
        run = self._engine.run(
            self._spec, self._runners,
            cache_checks=self._cache_checks, run_state=self._run_state,
        )
        self._emit_terminal(self.job_finished, run)

    def _on_error(self, msg: str) -> None:
        # JobEngine.run() doesn't raise for an individual step failing —
        # that's a FAILED StepOutcome, already reported via step_finished
        # above. An exception reaching here means something broke outside
        # any one step (e.g. set_runners() never called, or a spec with an
        # unknown dependency) — still emit exactly one terminal signal.
        self._emit_terminal(self.job_finished, self._run_state)
