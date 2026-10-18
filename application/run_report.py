"""A finished (or interrupted) recipe run, summed up for the run screen
(UI/UX audit 2026-10-18, UX-11 / T11): how many steps succeeded, were
reused from cache, failed, were cancelled or never got to run, why each
failure happened in the user's terms, and which materials exist now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from domain.job import StepStatus
from domain.run_report import classify_failure, redact_secrets

SUCCESS = "success"
PARTIAL = "partial"
CANCELLED = "cancelled"
FAILED = "failed"
RUNNING = "running"


@dataclass(frozen=True)
class StepFailure:
    step: str
    kind: str          # domain.run_report kind
    details: str       # raw error, secrets redacted


@dataclass(frozen=True)
class RunReport:
    state: str
    succeeded: tuple[str, ...]
    reused: tuple[str, ...]
    failed: tuple[StepFailure, ...]
    cancelled: tuple[str, ...]
    blocked: tuple[str, ...]     # did not run: a step they depend on did not succeed
    waiting: tuple[str, ...]     # no outcome yet
    materials: tuple[str, ...]   # history artifact types now available


def build_run_report(run: Any) -> RunReport:
    """Summarize *run* (an ``application.job_engine.JobRun``)."""
    from application.steps import artifact_types_for_steps

    planned = list(run.spec.steps)
    planned_names = {step.name for step in planned}
    outcomes = run.outcomes
    ok = (StepStatus.SUCCEEDED, StepStatus.SKIPPED)
    succeeded: list[str] = []
    reused: list[str] = []
    cancelled: list[str] = []
    blocked: list[str] = []
    waiting: list[str] = []
    failed: list[StepFailure] = []
    for step in planned:
        outcome = outcomes.get(step.name)
        if outcome is None:
            waiting.append(step.name)
        elif outcome.status is StepStatus.SUCCEEDED:
            succeeded.append(step.name)
        elif outcome.status is StepStatus.SKIPPED:
            reused.append(step.name)
        elif outcome.status is StepStatus.FAILED:
            failed.append(StepFailure(step.name, classify_failure(outcome.error),
                                      redact_secrets(outcome.error)))
        else:
            deps_ok = all(
                (dep := outcomes.get(name)) is not None and dep.status in ok
                for name in step.depends_on if name in planned_names
            )
            (cancelled if deps_ok else blocked).append(step.name)

    if waiting and not (failed or cancelled or blocked):
        state = RUNNING
    elif not (failed or cancelled or blocked or waiting):
        state = SUCCESS
    elif failed and not (succeeded or reused):
        state = FAILED
    elif cancelled and not failed:
        state = CANCELLED
    else:
        state = PARTIAL
    materials = tuple(sorted(artifact_types_for_steps(succeeded + reused)))
    return RunReport(state, tuple(succeeded), tuple(reused), tuple(failed),
                     tuple(cancelled), tuple(blocked), tuple(waiting), materials)
