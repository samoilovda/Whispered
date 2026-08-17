"""Job/step specs for a resumable, dependency-ordered pipeline engine.

See docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R8 — this is meant to replace
the orchestration currently split across the preset chain controller, the
YouTube/Insights workers' own extra-chain bookkeeping, and the two batch
mechanisms, so that e.g. `chapters` stops being computed twice when both
YouTube and Insights are enabled.

Qt-free: JobSpec/StepSpec describe *what* to run and in what order;
application/job_engine.py is what actually runs them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple


class StepStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"      # cache hit — an existing artifact was reused
    CANCELLED = "cancelled"  # job was cancelled, or a dependency didn't succeed


@dataclass(frozen=True)
class StepSpec:
    """One unit of work in a JobSpec's DAG.

    ``resource`` names a concurrency pool (e.g. ``"local_llm"``) that
    JobEngine limits — see its docstring for why local LM Studio
    completions default to a limit of 1. ``depends_on`` names other steps
    in the same JobSpec that must have SUCCEEDED or been SKIPPED (cache
    hit) before this one may start.
    """
    name: str
    resource: str = "default"
    depends_on: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class JobSpec:
    """A named set of steps and their dependency edges."""
    name: str
    steps: Tuple[StepSpec, ...]

    def __post_init__(self) -> None:
        names = [s.name for s in self.steps]
        if len(names) != len(set(names)):
            raise ValueError(f"JobSpec {self.name!r} has duplicate step names")
        known = set(names)
        for step in self.steps:
            missing = set(step.depends_on) - known
            if missing:
                raise ValueError(
                    f"JobSpec {self.name!r} step {step.name!r} depends on "
                    f"unknown step(s): {sorted(missing)}"
                )

    def topological_order(self) -> List[str]:
        """Step names ordered so every dependency precedes its dependents.

        Ties are broken alphabetically for a deterministic, testable
        order. Raises ``ValueError`` on a dependency cycle.
        """
        remaining = {s.name: set(s.depends_on) for s in self.steps}
        order: List[str] = []
        while remaining:
            ready = sorted(name for name, deps in remaining.items() if not deps)
            if not ready:
                raise ValueError(
                    f"JobSpec {self.name!r} has a dependency cycle among: "
                    f"{sorted(remaining)}"
                )
            for name in ready:
                order.append(name)
                del remaining[name]
            for deps in remaining.values():
                deps.difference_update(ready)
        return order


@dataclass
class StepOutcome:
    """Result of running, skipping, or cancelling one step."""
    name: str
    status: StepStatus
    error: str = ""
    result: object = None
