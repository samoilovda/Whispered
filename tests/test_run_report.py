"""domain.run_report / application.run_report (UX-11 / T11)."""

from __future__ import annotations

from application.job_engine import JobRun
from application.run_report import (
    CANCELLED, FAILED, PARTIAL, RUNNING, SUCCESS, build_run_report,
)
from domain.job import JobSpec, StepOutcome, StepSpec, StepStatus
from domain.run_report import (
    INVALID_OUTPUT, IO_ERROR, NEEDS_SETUP, PROVIDER_UNAVAILABLE, UNKNOWN,
    classify_failure, redact_secrets,
)


def test_classification():
    assert classify_failure("LM Studio is not responding.\nURL: http://x") == PROVIDER_UNAVAILABLE
    assert classify_failure("Read timed out. (read timeout=600)") == PROVIDER_UNAVAILABLE
    assert classify_failure("401 Unauthorized: invalid api key") == NEEDS_SETUP
    assert classify_failure("A custom prompt file is required for the custom book stage") == NEEDS_SETUP
    assert classify_failure("[Errno 13] Permission denied: '/out/book.md'") == IO_ERROR
    assert classify_failure("Expecting value: line 1 column 1 (char 0) — bad JSON") == INVALID_OUTPUT
    assert classify_failure("The book pipeline produced no complete result") == INVALID_OUTPUT
    assert classify_failure("something odd") == UNKNOWN


def test_secrets_are_redacted():
    text = redact_secrets("auth failed for sk-abcdefghijklmnop with Bearer abc.def.ghijkl api_key=SECRET123")
    assert "abcdefghijklmnop" not in text and "SECRET123" not in text and "abc.def.ghijkl" not in text
    assert "Bearer •••" in text and "api_key=•••" in text


def _run(outcomes, steps=("transcribe", "clean", "article", "insights")):
    deps = {"clean": ("transcribe",), "article": ("clean",), "insights": ("transcribe",)}
    spec = JobSpec(name="r", steps=tuple(StepSpec(n, depends_on=deps.get(n, ())) for n in steps))
    run = JobRun(spec=spec)
    run.outcomes.update({n: StepOutcome(n, s, error=e) for n, (s, e) in outcomes.items()})
    return run


def test_states_are_distinct():
    ok = (StepStatus.SUCCEEDED, "")
    assert build_run_report(_run({n: ok for n in ("transcribe", "clean", "article", "insights")})).state == SUCCESS
    partial = build_run_report(_run({
        "transcribe": ok, "clean": (StepStatus.FAILED, "LM Studio is not responding"),
        "article": (StepStatus.CANCELLED, ""), "insights": (StepStatus.SKIPPED, ""),
    }))
    assert partial.state == PARTIAL
    assert partial.blocked == ("article",)  # waits on the failed clean
    assert partial.reused == ("insights",)
    assert partial.failed[0].kind == PROVIDER_UNAVAILABLE
    assert partial.materials == ("insights",)
    cancelled = build_run_report(_run({"transcribe": ok, "clean": (StepStatus.CANCELLED, ""),
                                       "article": (StepStatus.CANCELLED, ""),
                                       "insights": (StepStatus.CANCELLED, "")}))
    assert cancelled.state == CANCELLED
    failed = build_run_report(_run({"transcribe": (StepStatus.FAILED, "boom")}, steps=("transcribe",)))
    assert failed.state == FAILED
    running = build_run_report(_run({"transcribe": ok}))
    assert running.state == RUNNING and running.waiting == ("clean", "article", "insights")
