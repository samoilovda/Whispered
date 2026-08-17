"""Unit tests for application/job_engine.py (R8, see
docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md).
"""

from __future__ import annotations

import threading
import time

from application.job_engine import JobEngine, JobRun
from domain.job import JobSpec, StepOutcome, StepSpec, StepStatus


def _spec(**kwargs) -> JobSpec:
    return JobSpec(name="test", **kwargs)


def test_all_steps_run_and_succeed():
    spec = _spec(steps=(StepSpec("a"), StepSpec("b", depends_on=("a",))))
    calls = []
    engine = JobEngine()
    run = engine.run(spec, {
        "a": lambda: calls.append("a") or "result-a",
        "b": lambda: calls.append("b") or "result-b",
    })
    assert calls == ["a", "b"]
    assert run.outcomes["a"] == StepOutcome("a", StepStatus.SUCCEEDED, result="result-a")
    assert run.outcomes["b"] == StepOutcome("b", StepStatus.SUCCEEDED, result="result-b")


def test_dependency_runs_before_dependent_even_with_reversed_thread_start():
    """b depends on a; b's runner must never observe a's effect as
    incomplete, regardless of thread scheduling — engine, not luck, must
    guarantee the order."""
    spec = _spec(steps=(StepSpec("a"), StepSpec("b", depends_on=("a",))))
    state = {"a_done": False}
    seen_a_done_from_b = []

    def run_a():
        time.sleep(0.05)
        state["a_done"] = True

    def run_b():
        seen_a_done_from_b.append(state["a_done"])

    JobEngine().run(spec, {"a": run_a, "b": run_b})
    assert seen_a_done_from_b == [True]


def test_missing_runner_raises_value_error():
    spec = _spec(steps=(StepSpec("a"), StepSpec("b")))
    try:
        JobEngine().run(spec, {"a": lambda: None})
    except ValueError as exc:
        assert "b" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing runner")


def test_failed_step_cancels_direct_and_transitive_dependents():
    spec = _spec(steps=(
        StepSpec("a"),
        StepSpec("b", depends_on=("a",)),
        StepSpec("c", depends_on=("b",)),
    ))

    def fail_a():
        raise RuntimeError("boom")

    calls = []
    run = JobEngine().run(spec, {
        "a": fail_a,
        "b": lambda: calls.append("b"),
        "c": lambda: calls.append("c"),
    })
    assert calls == []
    assert run.outcomes["a"].status is StepStatus.FAILED
    assert run.outcomes["a"].error == "boom"
    assert run.outcomes["b"].status is StepStatus.CANCELLED
    assert run.outcomes["c"].status is StepStatus.CANCELLED


def test_independent_branch_still_runs_after_sibling_fails():
    spec = _spec(steps=(
        StepSpec("a"),
        StepSpec("b", depends_on=("a",)),
        StepSpec("x"),
    ))

    def fail_a():
        raise RuntimeError("boom")

    calls = []
    run = JobEngine().run(spec, {
        "a": fail_a,
        "b": lambda: calls.append("b"),
        "x": lambda: calls.append("x"),
    })
    assert calls == ["x"]
    assert run.outcomes["b"].status is StepStatus.CANCELLED
    assert run.outcomes["x"].status is StepStatus.SUCCEEDED


def test_cache_hit_skips_the_runner():
    spec = _spec(steps=(StepSpec("cover"),))
    calls = []
    run = JobEngine().run(
        spec,
        {"cover": lambda: calls.append("cover")},
        cache_checks={"cover": lambda: True},
    )
    assert calls == []
    assert run.outcomes["cover"].status is StepStatus.SKIPPED


def test_skipped_dependency_still_unblocks_dependents():
    spec = _spec(steps=(StepSpec("a"), StepSpec("b", depends_on=("a",))))
    calls = []
    run = JobEngine().run(
        spec,
        {"a": lambda: None, "b": lambda: calls.append("b")},
        cache_checks={"a": lambda: True},
    )
    assert run.outcomes["a"].status is StepStatus.SKIPPED
    assert calls == ["b"]
    assert run.outcomes["b"].status is StepStatus.SUCCEEDED


def test_cache_check_exception_falls_back_to_running_the_step():
    spec = _spec(steps=(StepSpec("a"),))
    calls = []

    def broken_check():
        raise OSError("disk error")

    run = JobEngine().run(
        spec,
        {"a": lambda: calls.append("a")},
        cache_checks={"a": broken_check},
    )
    assert calls == ["a"]
    assert run.outcomes["a"].status is StepStatus.SUCCEEDED


def test_cancel_before_run_marks_every_step_cancelled():
    spec = _spec(steps=(StepSpec("a"), StepSpec("b", depends_on=("a",))))
    calls = []
    run_state = JobRun(spec=spec)
    run_state.cancel()
    JobEngine().run(spec, {
        "a": lambda: calls.append("a"),
        "b": lambda: calls.append("b"),
    }, run_state=run_state)
    assert calls == []
    assert run_state.outcomes["a"].status is StepStatus.CANCELLED
    assert run_state.outcomes["b"].status is StepStatus.CANCELLED


def test_cancel_mid_run_stops_remaining_steps():
    spec = _spec(steps=(StepSpec("a"), StepSpec("b", depends_on=("a",)), StepSpec("c", depends_on=("b",))))
    run_state = JobRun(spec=spec)
    calls = []

    def run_a():
        calls.append("a")
        run_state.cancel()

    def run_b():
        calls.append("b")

    JobEngine().run(spec, {
        "a": run_a,
        "b": run_b,
        "c": lambda: calls.append("c"),
    }, run_state=run_state)
    assert calls == ["a"]
    assert run_state.outcomes["a"].status is StepStatus.SUCCEEDED
    assert run_state.outcomes["b"].status is StepStatus.CANCELLED
    assert run_state.outcomes["c"].status is StepStatus.CANCELLED


def test_resume_only_runs_steps_without_an_outcome():
    spec = _spec(steps=(StepSpec("a"), StepSpec("b", depends_on=("a",))))
    run_state = JobRun(spec=spec)
    run_state.outcomes["a"] = StepOutcome("a", StepStatus.SUCCEEDED, result="cached-a")

    calls = []
    JobEngine().run(spec, {
        "a": lambda: calls.append("a"),
        "b": lambda: calls.append("b"),
    }, run_state=run_state)
    assert calls == ["b"]  # a's runner never called — already resolved


def test_reset_step_allows_retry_after_failure():
    spec = _spec(steps=(StepSpec("a"), StepSpec("b", depends_on=("a",))))
    run_state = JobRun(spec=spec)
    attempts = {"a": 0}

    def flaky_a():
        attempts["a"] += 1
        if attempts["a"] == 1:
            raise RuntimeError("transient")
        return "ok"

    engine = JobEngine()
    calls = []
    engine.run(spec, {"a": flaky_a, "b": lambda: calls.append("b")}, run_state=run_state)
    assert run_state.outcomes["a"].status is StepStatus.FAILED
    assert run_state.outcomes["b"].status is StepStatus.CANCELLED
    assert calls == []

    # Retry: reset the failed step (and its cancelled dependent) and rerun.
    run_state.reset_step("a")
    assert "a" not in run_state.outcomes
    assert "b" not in run_state.outcomes  # transitively reset too

    engine.run(spec, {"a": flaky_a, "b": lambda: calls.append("b")}, run_state=run_state)
    assert run_state.outcomes["a"].status is StepStatus.SUCCEEDED
    assert run_state.outcomes["b"].status is StepStatus.SUCCEEDED
    assert calls == ["b"]


def test_resource_limit_serializes_steps_sharing_a_pool():
    """Three independent steps all using the default "local_llm" limit of
    1 must never run concurrently — this is the whole reason the resource
    pool exists (see core/lm_client.py's _serialized_completion)."""
    spec = _spec(steps=(
        StepSpec("x", resource="local_llm"),
        StepSpec("y", resource="local_llm"),
        StepSpec("z", resource="local_llm"),
    ))
    active = {"count": 0, "max": 0}
    lock = threading.Lock()

    def make_runner():
        def runner():
            with lock:
                active["count"] += 1
                active["max"] = max(active["max"], active["count"])
            time.sleep(0.03)
            with lock:
                active["count"] -= 1
        return runner

    JobEngine(resource_limits={"local_llm": 1}).run(spec, {
        "x": make_runner(), "y": make_runner(), "z": make_runner(),
    })
    assert active["max"] == 1


def test_independent_steps_in_different_pools_run_concurrently():
    """Two independent steps in *different* resource pools should be free
    to overlap — the point of per-resource limits, not a single global
    lock."""
    spec = _spec(steps=(
        StepSpec("cover", resource="cpu"),
        StepSpec("llm", resource="local_llm"),
    ))
    barrier = threading.Barrier(2, timeout=2.0)
    reached = {"cover": False, "llm": False}

    def cover_runner():
        reached["cover"] = True
        barrier.wait()

    def llm_runner():
        reached["llm"] = True
        barrier.wait()

    # Both runners block on a 2-party barrier — if the engine only ran one
    # at a time, this would deadlock and the test would fail on timeout.
    JobEngine(resource_limits={"local_llm": 1, "cpu": 1}).run(spec, {
        "cover": cover_runner, "llm": llm_runner,
    })
    assert reached == {"cover": True, "llm": True}
