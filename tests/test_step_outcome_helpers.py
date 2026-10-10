"""step_outcome_result / summarize_run, and application.records."""

from types import SimpleNamespace

import pytest

import application.steps as steps
from application.records import add_record_badges
from application.steps import step_outcome_result, summarize_run
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


def test_add_record_badges_merges_with_existing(monkeypatch):
    store = _Store(["book"])
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    add_record_badges(7, {"youtube"})
    assert store.saved == (7, ["book", "transcript", "youtube"])


def test_add_record_badges_skips_nothing_to_record(monkeypatch):
    store = _Store([])
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    add_record_badges(None, {"youtube"})
    add_record_badges(7, set())
    assert store.saved is None


class _NewRecordStore:
    def __init__(self):
        self.calls = []

    def add(self, result, **kwargs):
        self.calls.append(("add", kwargs["source_path"], kwargs["source_kind"]))
        return 42

    def save_current_revision(self, record_id, result, speaker_names, keep):
        self.calls.append(("revision", record_id, keep))


def test_save_new_record_adds_the_record_and_its_first_version(monkeypatch):
    import config
    from application.records import save_new_record

    store = _NewRecordStore()
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    monkeypatch.setattr(config, "_config", config.Config(transcript_revisions_kept=5))
    assert save_new_record(object(), source_path="/a.mp3", model="tiny",
                           source_kind="live") == 42
    assert store.calls == [("add", "/a.mp3", "live"), ("revision", 42, 5)]


def test_save_new_record_respects_history_off(monkeypatch):
    import config
    from application.records import save_new_record

    store = _NewRecordStore()
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    monkeypatch.setattr(config, "_config", config.Config(history_enabled=False))
    assert save_new_record(object(), source_path="/a.mp3", model="tiny") is None
    assert store.calls == []


def test_finalize_live_record_replaces_the_result_and_saves_a_version(monkeypatch):
    import config
    from application.records import finalize_live_record

    calls = []

    class _Store:
        def update_result(self, record_id, result, speaker_names):
            calls.append(("update", record_id, speaker_names))

        def save_current_revision(self, record_id, result, speaker_names, keep):
            calls.append(("revision", record_id, keep))

    monkeypatch.setattr("core.history.get_history_store", lambda: _Store())
    monkeypatch.setattr(config, "_config", config.Config(transcript_revisions_kept=3))
    result = SimpleNamespace(speaker_names={"S1": "Ann"})
    assert finalize_live_record(9, result) is True
    assert calls == [("update", 9, {"S1": "Ann"}), ("revision", 9, 3)]
