"""step_outcome_result / summarize_run / record_run_artifacts."""

from types import SimpleNamespace

import pytest

import application.steps as steps
from application.steps import record_run_artifacts, step_outcome_result, summarize_run
from domain.job import StepOutcome, StepStatus


def _run(*outcomes: StepOutcome):
    return SimpleNamespace(outcomes={o.name: o for o in outcomes})


def test_succeeded_result_of_the_expected_type():
    run = _run(StepOutcome("insights", StepStatus.SUCCEEDED, result={"a": 1}))
    assert step_outcome_result(run, "insights", None, dict) == ({"a": 1}, "")


def test_skipped_step_loads_its_artifact(monkeypatch):
    context = object()
    monkeypatch.setattr(steps, "load_step_result", lambda ctx, name: {"from": name})
    run = _run(StepOutcome("youtube_package", StepStatus.SKIPPED))
    assert step_outcome_result(run, "youtube_package", context, dict) == (
        {"from": "youtube_package"}, "")


def test_skipped_without_context_or_with_a_wrong_type_is_no_result():
    run = _run(StepOutcome("clean", StepStatus.SKIPPED))
    assert step_outcome_result(run, "clean", None, dict) == (None, "")
    run = _run(StepOutcome("clean", StepStatus.SUCCEEDED, result="text"))
    assert step_outcome_result(run, "clean", None, dict) == (None, "")


@pytest.mark.parametrize("status", [StepStatus.FAILED, StepStatus.CANCELLED])
def test_failed_step_reports_its_error(status):
    run = _run(StepOutcome("book", status, error="boom", result={"x": 1}))
    assert step_outcome_result(run, "book", None, dict) == (None, "boom")


def test_missing_outcome():
    assert step_outcome_result(_run(), "clean", None, dict) == (None, "")


def test_summarize_run():
    run = _run(
        StepOutcome("transcribe", StepStatus.SKIPPED),
        StepOutcome("youtube_package", StepStatus.SUCCEEDED),
        StepOutcome("insights", StepStatus.FAILED, error="x"),
    )
    summary = summarize_run(run)
    assert summary.succeeded == {"transcribe", "youtube_package"}
    assert summary.had_error
    assert summary.artifact_types == {"youtube"}


class _Store:
    def __init__(self, artifacts):
        self.saved = None
        self._artifacts = artifacts

    def get_record(self, record_id):
        return {"artifacts": self._artifacts}

    def set_artifacts(self, record_id, artifacts):
        self.saved = (record_id, artifacts)


def test_record_run_artifacts_merges_with_existing(monkeypatch):
    store = _Store(["book"])
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    record_run_artifacts(7, {"youtube"})
    assert store.saved == (7, ["book", "transcript", "youtube"])


def test_record_run_artifacts_skips_nothing_to_record(monkeypatch):
    store = _Store([])
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    record_run_artifacts(None, {"youtube"})
    record_run_artifacts(7, set())
    assert store.saved is None
