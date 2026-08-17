"""Unit tests for domain/job.py (R8, see
docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md).
"""

from __future__ import annotations

import pytest

from domain.job import JobSpec, StepSpec


def test_topological_order_respects_dependencies():
    spec = JobSpec(
        name="chain",
        steps=(
            StepSpec("transcribe"),
            StepSpec("chapters", depends_on=("transcribe",)),
            StepSpec("youtube", depends_on=("chapters",)),
            StepSpec("insights", depends_on=("chapters",)),
        ),
    )
    order = spec.topological_order()
    assert order.index("transcribe") < order.index("chapters")
    assert order.index("chapters") < order.index("youtube")
    assert order.index("chapters") < order.index("insights")


def test_topological_order_is_deterministic_for_ties():
    spec = JobSpec(name="fan_out", steps=(StepSpec("b"), StepSpec("a"), StepSpec("c")))
    assert spec.topological_order() == ["a", "b", "c"]


def test_topological_order_raises_on_cycle():
    spec = JobSpec(
        name="cycle",
        steps=(
            StepSpec("a", depends_on=("b",)),
            StepSpec("b", depends_on=("a",)),
        ),
    )
    with pytest.raises(ValueError, match="cycle"):
        spec.topological_order()


def test_duplicate_step_names_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        JobSpec(name="dup", steps=(StepSpec("a"), StepSpec("a")))


def test_unknown_dependency_rejected():
    with pytest.raises(ValueError, match="unknown"):
        JobSpec(name="bad_dep", steps=(StepSpec("a", depends_on=("nope",)),))
