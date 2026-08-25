"""Real-Qt tests for core/job_runner.py (see
docs/UI_REDESIGN_PLAN_2026-09.ru.md, B1).
"""

from __future__ import annotations

import threading

from application.job_engine import JobRun
from core.job_runner import JobRunner
from domain.job import JobSpec, StepSpec, StepStatus


def _spec(**kwargs) -> JobSpec:
    return JobSpec(name="test", **kwargs)


def test_run_does_not_block_the_ui_thread(process_events):
    """A step blocked inside JobEngine's background thread must never
    freeze the caller's ability to pump the Qt event loop — proof that
    JobEngine.run() (a single blocking call) is genuinely running off the
    UI thread, not on it."""
    release = threading.Event()
    started = threading.Event()

    def slow_step():
        started.set()
        release.wait(5)
        return "done"

    runner = JobRunner(_spec(steps=(StepSpec("a"),)))
    runner.set_runners({"a": slow_step})
    finished: list = []
    runner.job_finished.connect(lambda run: finished.append(run))

    runner.start()
    try:
        assert started.wait(2), "step never started"

        # The step is blocked on release.wait() right now. If run() had
        # been called directly on this thread instead of inside the
        # QThread, every processEvents() call below would hang until the
        # 5s wait timed out.
        for _ in range(20):
            process_events()
        assert not finished, "job finished despite the step still being blocked"

        release.set()
        assert runner.wait(2000)
        process_events()
        assert len(finished) == 1
    finally:
        release.set()
        runner.wait(2000)


def test_cancel_mid_run_marks_unresolved_dependents_cancelled(process_events):
    release = threading.Event()
    started = threading.Event()

    def slow_a():
        started.set()
        release.wait(5)
        return "a-result"

    def b():
        raise AssertionError("b must never run once the job is cancelled")

    runner = JobRunner(_spec(steps=(StepSpec("a"), StepSpec("b", depends_on=("a",)))))
    runner.set_runners({"a": slow_a, "b": b})
    finished: list = []
    runner.job_finished.connect(lambda run: finished.append(run))

    runner.start()
    try:
        assert started.wait(2)
        runner.cancel()
        # "a"'s runner is already in flight — cancellation is cooperative,
        # so it still completes once released; "b" was never ready to
        # start (its dependency hadn't resolved) and must come back
        # CANCELLED rather than running at all.
        release.set()
        assert runner.wait(2000)
        process_events()

        assert len(finished) == 1
        run = finished[0]
        assert run.outcomes["a"].status == StepStatus.SUCCEEDED
        assert run.outcomes["b"].status == StepStatus.CANCELLED
    finally:
        release.set()
        runner.wait(2000)


def test_cache_hit_step_is_skipped_and_its_runner_never_called(process_events):
    calls: list = []

    def runner_a():
        calls.append("a")
        return "result"

    runner = JobRunner(_spec(steps=(StepSpec("a"),)))
    runner.set_runners({"a": runner_a}, cache_checks={"a": lambda: True})
    finished: list = []
    runner.job_finished.connect(lambda run: finished.append(run))

    runner.start()
    assert runner.wait(2000)
    process_events()

    assert calls == []
    assert finished[0].outcomes["a"].status == StepStatus.SKIPPED


def test_failed_step_cancels_its_dependents_instead_of_running_them(process_events):
    def fail_a():
        raise RuntimeError("boom")

    def b():
        raise AssertionError("b must never run after a fails")

    runner = JobRunner(_spec(steps=(StepSpec("a"), StepSpec("b", depends_on=("a",)))))
    runner.set_runners({"a": fail_a, "b": b})
    finished: list = []
    runner.job_finished.connect(lambda run: finished.append(run))

    runner.start()
    assert runner.wait(2000)
    process_events()

    run = finished[0]
    assert run.outcomes["a"].status == StepStatus.FAILED
    assert run.outcomes["a"].error == "boom"
    assert run.outcomes["b"].status == StepStatus.CANCELLED


def test_signals_fire_with_the_right_step_name_and_payload(process_events):
    events: list = []

    runner = JobRunner(_spec(steps=(StepSpec("a"),)))
    progress_cb = runner.make_progress_callback("a")

    def runner_a():
        progress_cb(50, "halfway")
        return "ok"

    runner.set_runners({"a": runner_a})
    runner.step_started.connect(lambda name: events.append(("started", name)))
    runner.step_progress.connect(
        lambda name, pct, msg: events.append(("progress", name, pct, msg))
    )
    runner.step_finished.connect(
        lambda name, outcome: events.append(("finished", name, outcome.status))
    )

    runner.start()
    assert runner.wait(2000)
    process_events()

    assert ("started", "a") in events
    assert ("progress", "a", 50, "halfway") in events
    assert ("finished", "a", StepStatus.SUCCEEDED) in events


def test_disconnect_business_signals_needs_no_override(process_events):
    """JobRunner's terminal signal is job_finished, not finished — unlike
    core/insights_worker.py's InsightsWorker, nothing here shadows
    QThread's own built-in signal, so WorkerRegistry's generic by-name
    sweep (which skips only a signal literally named "finished") should
    already disconnect all four business signals with no override
    needed (see the class docstring)."""
    runner = JobRunner(_spec(steps=(StepSpec("a"),)))
    runner.set_runners({"a": lambda: "ok"})
    calls: list = []
    runner.step_started.connect(lambda name: calls.append(("started", name)))
    runner.step_progress.connect(lambda *args: calls.append(("progress", *args)))
    runner.step_finished.connect(lambda *args: calls.append(("finished", *args)))
    runner.job_finished.connect(lambda run: calls.append(("job_finished", run)))

    runner._disconnect_business_signals()

    runner.start()
    assert runner.wait(2000)
    process_events()

    assert calls == []


def test_resuming_a_job_run_only_reruns_the_reset_step(process_events):
    """JobRun.reset_step() (already covered by tests/test_job_engine.py)
    plus a second JobRunner sharing the same run_state is how a UI-level
    "Retry" (docs/UI_REDESIGN_PLAN_2026-09.ru.md, B4) is meant to work."""
    calls: list = []

    def a():
        calls.append("a")
        return "a"

    def b():
        calls.append("b")
        return "b"

    spec = _spec(steps=(StepSpec("a"), StepSpec("b", depends_on=("a",))))
    run_state = JobRun(spec=spec)

    first = JobRunner(spec, run_state=run_state)
    first.set_runners({"a": a, "b": b})
    first.start()
    assert first.wait(2000)
    process_events()
    assert calls == ["a", "b"]

    run_state.reset_step("b")
    calls.clear()

    second = JobRunner(spec, run_state=run_state)
    second.set_runners({"a": a, "b": b})
    second.start()
    assert second.wait(2000)
    process_events()

    assert calls == ["b"]
    assert run_state.outcomes["a"].status == StepStatus.SUCCEEDED
    assert run_state.outcomes["b"].status == StepStatus.SUCCEEDED
