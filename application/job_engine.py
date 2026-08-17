"""Executes a JobSpec's steps in dependency order, with per-resource
concurrency limits, cache-skip (via domain.artifact.Artifact), retry, and
cancellation.

See docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R8.

Independent steps (no dependency edge between them) run concurrently, each
in its own thread; a step only starts once every step it depends on has
resolved. Resource pools (e.g. ``"local_llm"``) cap how many steps using
that pool run at once *across the whole engine* — that's the point of
having them: a Cover-rendering step and a chapters-generation step can run
at the same time, but two steps that both hit LM Studio never do, without
the caller having to hand-serialize unrelated generators the way
`_serialized_completion` in core/lm_client.py currently does per-call.

Scope of this first version — deliberately not attempted here, tracked as
open follow-up (mirrors how R5-full's Artifact migration was scoped):
- Not wired into any real generator yet (preset chain, YouTube, Insights,
  book pipeline). Migrating those is separate, incremental work; forcing
  it into the same change as the engine itself would be exactly the kind
  of large, hard-to-review, hard-to-revert step the audit plan warns
  against.
- No SQLite-persisted state — "resume" here means continuing a JobRun
  object still held in memory (e.g. after a step failed or was
  cancelled), not surviving a process restart/crash. JobRun.outcomes is a
  plain dict a caller could serialize, but this module doesn't do it.
- Retry is "the caller calls JobRun.reset_step() and calls run() again",
  not automatic retry-with-backoff.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from core.logger import get_logger
from domain.job import JobSpec, StepOutcome, StepSpec, StepStatus

logger = get_logger(__name__)

StepRunner = Callable[[], object]
"""A step's actual work: takes nothing, returns whatever the caller wants
recorded as StepOutcome.result, raises on failure."""

CacheCheck = Callable[[], bool]
"""Returns True if a step's prior output can be reused instead of (re)run
— typically infrastructure.persistence.artifact_store.is_cache_valid()
bound to that step's expected Artifact."""


@dataclass
class JobRun:
    """Mutable state for one JobSpec execution: outcomes recorded so far,
    and the cooperative cancellation flag every step's runner should
    honour if it can. Passing the same JobRun back into JobEngine.run()
    resumes — steps that already have an outcome are not re-run.
    """
    spec: JobSpec
    outcomes: Dict[str, StepOutcome] = field(default_factory=dict)
    _cancelled: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def _record(self, outcome: StepOutcome) -> None:
        with self._lock:
            self.outcomes[outcome.name] = outcome

    def reset_step(self, name: str) -> None:
        """Discard *name*'s outcome so the next run() call retries it —
        along with any step that was itself marked CANCELLED only because
        *name* hadn't succeeded yet, so they get reconsidered too.
        """
        if name not in self.outcomes:
            return
        del self.outcomes[name]
        for outcome in list(self.outcomes.values()):
            step = next((s for s in self.spec.steps if s.name == outcome.name), None)
            if (
                step is not None
                and name in step.depends_on
                and outcome.status is StepStatus.CANCELLED
            ):
                self.reset_step(outcome.name)


class JobEngine:
    """Runs JobSpecs. One instance owns its resource pools' concurrency
    limits and the semaphores enforcing them across concurrently-running
    steps.
    """

    def __init__(self, resource_limits: Optional[Dict[str, int]] = None) -> None:
        # Local LM Studio completions are serialized process-wide — see
        # the CLAUDE.md rule on not firing parallel requests at it (the
        # server can stop responding while `lms server status` still says
        # running). Cloud providers aren't affected by that limit.
        self._resource_limits = dict(resource_limits or {"local_llm": 1})
        self._resource_locks: Dict[str, threading.Semaphore] = {
            name: threading.Semaphore(limit)
            for name, limit in self._resource_limits.items()
        }

    def run(
        self,
        spec: JobSpec,
        runners: Dict[str, StepRunner],
        *,
        cache_checks: Optional[Dict[str, CacheCheck]] = None,
        run_state: Optional[JobRun] = None,
    ) -> JobRun:
        """Execute every step in *spec* that doesn't already have an
        outcome in *run_state*.

        Steps run as soon as every dependency has resolved — independent
        steps run concurrently. *runners* must have an entry for every
        step in *spec*. *cache_checks*, if given, maps step name ->
        callable returning True when that step's prior output can be
        reused; such a step is marked SKIPPED and its runner is never
        called. A step whose dependency did not SUCCEED/SKIP (failed, was
        cancelled, or the run itself was cancelled) is marked CANCELLED
        without running — this is what keeps one failure from silently
        letting downstream steps assume it worked.
        """
        missing_runners = {s.name for s in spec.steps} - set(runners)
        if missing_runners:
            raise ValueError(f"No runner provided for step(s): {sorted(missing_runners)}")
        spec.topological_order()  # fail fast on an unknown dep / cycle

        run_state = run_state if run_state is not None else JobRun(spec=spec)
        cache_checks = cache_checks or {}
        steps_by_name = {s.name: s for s in spec.steps}

        while True:
            ready = self._ready_steps(steps_by_name, run_state)
            if not ready:
                break
            threads: List[threading.Thread] = []
            for step in ready:
                thread = threading.Thread(
                    target=self._resolve_step,
                    args=(step, runners[step.name], cache_checks.get(step.name), run_state),
                    daemon=True,
                )
                threads.append(thread)
                thread.start()
            for thread in threads:
                thread.join()

        return run_state

    @staticmethod
    def _ready_steps(steps_by_name: Dict[str, StepSpec], run_state: JobRun) -> List[StepSpec]:
        return [
            step for name, step in steps_by_name.items()
            if name not in run_state.outcomes
            and all(dep in run_state.outcomes for dep in step.depends_on)
        ]

    def _resolve_step(
        self,
        step: StepSpec,
        runner: StepRunner,
        cache_check: Optional[CacheCheck],
        run_state: JobRun,
    ) -> None:
        if run_state.is_cancelled():
            run_state._record(StepOutcome(step.name, StepStatus.CANCELLED))
            return
        blocking = [
            dep for dep in step.depends_on
            if run_state.outcomes[dep].status not in (StepStatus.SUCCEEDED, StepStatus.SKIPPED)
        ]
        if blocking:
            run_state._record(StepOutcome(step.name, StepStatus.CANCELLED))
            return
        if cache_check is not None and self._safe_cache_check(cache_check, step.name):
            run_state._record(StepOutcome(step.name, StepStatus.SKIPPED))
            return
        run_state._record(self._run_step(step, runner, run_state))

    def _run_step(self, step: StepSpec, runner: StepRunner, run_state: JobRun) -> StepOutcome:
        lock = self._resource_locks.get(step.resource)
        if lock is not None:
            lock.acquire()
        try:
            if run_state.is_cancelled():
                return StepOutcome(step.name, StepStatus.CANCELLED)
            try:
                result = runner()
            except Exception as exc:
                logger.warning("Job step %r failed: %s", step.name, exc)
                return StepOutcome(step.name, StepStatus.FAILED, error=str(exc))
            return StepOutcome(step.name, StepStatus.SUCCEEDED, result=result)
        finally:
            if lock is not None:
                lock.release()

    @staticmethod
    def _safe_cache_check(cache_check: CacheCheck, step_name: str) -> bool:
        try:
            return bool(cache_check())
        except Exception as exc:
            logger.warning("Job step %r cache check failed, will run: %s", step_name, exc)
            return False
