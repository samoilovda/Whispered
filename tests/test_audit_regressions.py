"""Regression scenarios from the 2026-10-17 audit, without external APIs."""

from __future__ import annotations

import dataclasses
import json
import runpy
import threading
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.job_engine import JobEngine, JobRun
from application.steps import STEP_REGISTRY, StepContext, build_cache_checks, build_job_spec, llm_params
from config import Config
from core.ai_provider import ProviderSettings, provider_from_config
from core.history import HistoryStore
from domain.artifact import Artifact
from domain.artifact_provenance import transcript_revision
from domain.cancellation import OperationCancelled
from domain.job import StepOutcome, StepStatus
from domain.transcription import Segment, TranscriptionResult
from infrastructure.persistence import artifact_store


def _result():
    return TranscriptionResult([Segment(0, 5, "Synthetic meeting", speaker="S1")], "en", 5)


def _context(tmp_path, **params):
    return StepContext("", _result(), "1", tmp_path, params={"model": "test-model", **params})


def test_recipe_insights_never_selects_youtube_cloud_provider(tmp_path, monkeypatch):
    cfg = Config(yt_provider="openai", yt_openai_api_key="synthetic-only")
    context = _context(tmp_path, **llm_params(cfg, _result(), provider=provider_from_config(cfg)))
    selected = []

    class LocalClient:
        def __init__(self, url):
            selected.append(url)

        def chat_completion_stream(self, **kwargs):
            return "[]"

    def forbidden(ps):
        pytest.fail("Insights attempted to create a YouTube cloud client")

    local_module = types.ModuleType("core.lm_client")
    local_module.LMStudioClient = LocalClient
    monkeypatch.setitem(sys.modules, "core.lm_client", local_module)
    monkeypatch.setattr("core.ai_provider.create_client", forbidden)
    STEP_REGISTRY["insights"].make_runner(context)()
    assert selected == [cfg.lm_studio_url] * 3
    assert artifact_store.load(tmp_path / "insights.json").provider == "lmstudio"


@pytest.mark.parametrize("step", ["insights", "youtube_package"])
def test_cancelled_generation_preserves_previous_output_and_manifest(tmp_path, monkeypatch, step):
    cancelled = threading.Event()
    context = dataclasses.replace(_context(tmp_path), is_cancelled=cancelled.is_set)
    artifact = STEP_REGISTRY[step].make_artifact(context)
    artifact_store.write_text(artifact, '{"previous": "successful"}')
    previous_manifest = artifact_store.manifest_path_for(artifact.path).read_bytes()

    def cancel_during_generation(*args, **kwargs):
        cancelled.set()
        return []

    monkeypatch.setattr("core.insights.generate_insight", cancel_during_generation)
    spec = build_job_spec("cancel", (step,))
    run = JobEngine().run(spec, {step: STEP_REGISTRY[step].make_runner(context)})
    assert run.outcomes[step].status is StepStatus.CANCELLED
    assert Path(artifact.path).read_text() == '{"previous": "successful"}'
    assert artifact_store.manifest_path_for(artifact.path).read_bytes() == previous_manifest


@pytest.mark.parametrize("stage", ["unwrap", "custom"])
def test_failed_book_is_failed_without_overwriting_previous_book(tmp_path, monkeypatch, stage):
    from book_pipeline import BookResult, BookStageResult

    context = _context(tmp_path)
    artifact = STEP_REGISTRY["book"].make_artifact(context)
    artifact_store.write_text(artifact, "previous complete book")
    stages = [BookStageResult("unwrap", "partial", "") ] if stage == "custom" else []
    stages.append(BookStageResult(stage, "", "", False, "synthetic stage failure"))
    monkeypatch.setattr("book_pipeline.BookPipeline", lambda: SimpleNamespace(
        process=lambda *a, **k: BookResult("", stages),
    ))
    run = JobEngine().run(build_job_spec("book", ("book",)), {"book": STEP_REGISTRY["book"].make_runner(context)})
    assert run.outcomes["book"].status is StepStatus.FAILED
    assert "synthetic stage failure" in run.outcomes["book"].error
    assert (tmp_path / "book.md").read_text() == "previous complete book"


@pytest.mark.parametrize("field,value", [("start", 120), ("end", 130), ("speaker", "S2"), ("text", "edited")])
def test_document_revision_tracks_segment_edits(field, value):
    original = _result()
    changed = dataclasses.replace(original.segments[0], **{field: value})
    assert transcript_revision(original.segments, "en") != transcript_revision([changed], "en")


def test_speaker_renaming_gets_a_restorable_history_revision(tmp_path):
    result = _result()
    store = HistoryStore(tmp_path / "history.db")
    record = store.add(result, "synthetic.wav")
    store.save_current_revision(record, result, {"S1": "Alice"})
    store.save_current_revision(record, result, {"S1": "Bob"})
    versions = store.list_transcript_revisions(record)
    assert len(versions) == 2
    payloads = [store.get_transcript_revision(v.id) for v in versions]
    assert {p["speaker_names"]["S1"] for p in payloads} == {"Alice", "Bob"}


@pytest.mark.parametrize("params", [
    {"do_unwrap": False}, {"do_custom": True}, {"book_temperature": 0.8},
    {"book_lm_url": "http://localhost:5678/v1"}, {"book_model": "other-model"},
])
def test_book_cache_changes_with_generation_settings(tmp_path, params):
    old = STEP_REGISTRY["book"].make_artifact(_context(tmp_path))
    new = STEP_REGISTRY["book"].make_artifact(_context(tmp_path, **params))
    assert old.cache_key() != new.cache_key()


def test_custom_prompt_edits_and_clean_output_invalidate_book(tmp_path):
    prompt = tmp_path / "custom.md"
    prompt.write_text("first instruction")
    context = _context(tmp_path, do_custom=True, custom_prompt_path=str(prompt))
    first = STEP_REGISTRY["book"].make_artifact(context)
    prompt.write_text("different instruction")
    second = STEP_REGISTRY["book"].make_artifact(context)
    assert first.cache_key() != second.cache_key()
    cleaned = SimpleNamespace(coherent=SimpleNamespace(text="edited cleaned text"))
    context = dataclasses.replace(context, get_result=lambda name: cleaned)
    assert second.cache_key() != STEP_REGISTRY["book"].make_artifact(context).cache_key()


def test_book_cannot_commit_an_omitted_requested_stage(tmp_path, monkeypatch):
    from book_pipeline import BookResult, BookStageResult

    context = _context(tmp_path, do_custom=True, custom_prompt_path=str(tmp_path / "custom.md"))
    monkeypatch.setattr("book_pipeline.BookPipeline", lambda: SimpleNamespace(
        process=lambda *a, **k: BookResult("", [BookStageResult("unwrap", "only first stage", "")]),
    ))
    with pytest.raises(RuntimeError, match="complete result"):
        STEP_REGISTRY["book"].make_runner(context)()
    assert not (tmp_path / "book.md").exists()


def test_auto_model_change_invalidates_new_run_and_unknown_models_never_hit(tmp_path, monkeypatch):
    module = types.ModuleType("core.lm_client")
    selected = ["model-a"]

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def get_loaded_model(self, timeout=2):
            return selected[0]

    module.LMStudioClient = Client
    monkeypatch.setitem(sys.modules, "core.lm_client", module)
    context = _context(tmp_path, model="")
    check = build_cache_checks(context, ("clean",))["clean"]
    assert not check()
    artifact = STEP_REGISTRY["clean"].make_artifact(context)
    artifact_store.write_text(artifact, "from model-a")
    assert check()
    selected[0] = "model-b"
    assert not build_cache_checks(_context(tmp_path, model=""), ("clean",))["clean"]()
    selected[0] = ""
    unknown = _context(tmp_path, model="")
    artifact_store.write_text(STEP_REGISTRY["clean"].make_artifact(unknown), "unidentified")
    assert not build_cache_checks(unknown, ("clean",))["clean"]()


@pytest.mark.parametrize("step,params", [("clean", {"use_ai": False}), ("article", {"article_formats": ["faq"]})])
def test_clean_and_article_options_invalidate_cache(tmp_path, step, params):
    assert (STEP_REGISTRY[step].make_artifact(_context(tmp_path)).cache_key()
            != STEP_REGISTRY[step].make_artifact(_context(tmp_path, **params)).cache_key())


def test_article_cache_uses_dependency_output_at_check_time(tmp_path):
    cleaned = SimpleNamespace(coherent=SimpleNamespace(text="original clean"))
    context = dataclasses.replace(_context(tmp_path), get_result=lambda name: cleaned)
    artifact = STEP_REGISTRY["article"].make_artifact(context)
    artifact_store.write_text(artifact, '{"blog":{"title":"T","content":"body"}}')
    check = build_cache_checks(context, ("article",))["article"]
    assert check()
    cleaned.coherent.text = "new generation of clean"
    assert not check()


def test_regenerating_a_predecessor_reruns_successful_transitive_descendants():
    spec = build_job_spec("recipe", ("transcribe", "clean", "article"))
    run = JobRun(spec)
    for step in spec.steps:
        run.outcomes[step.name] = StepOutcome(step.name, StepStatus.SUCCEEDED, result="old")
    run.reset_step("clean")
    calls = []
    JobEngine().run(spec, {s.name: lambda name=s.name: calls.append(name) for s in spec.steps}, run_state=run)
    assert calls == ["clean", "article"]
    assert run.outcomes["transcribe"].result == "old"


@pytest.mark.parametrize("operation", ["delete", "clear"])
def test_deleting_history_removes_runs_in_same_database(tmp_path, operation):
    from application.run_store import load_runs_for_record, save_run

    store = HistoryStore(tmp_path / "history.db")
    record = store.add(_result(), "synthetic.wav")
    save_run(record, "recipe", JobRun(build_job_spec("recipe", ("transcribe",))), db_path=store._db_path)
    store.delete(record) if operation == "delete" else store.clear()
    assert load_runs_for_record(record, db_path=store._db_path) == []


def test_config_cli_reveals_only_secret_presence(tmp_path, monkeypatch, capsys):
    import config

    path = tmp_path / "config.json"
    values = {name: f"SYNTHETIC-ONLY-{name}" for name in config._SECRET_FIELDS}
    path.write_text(json.dumps(values))
    monkeypatch.setattr("core.paths.config_dir", lambda: tmp_path)
    monkeypatch.setattr("core.paths.config_path", lambda: path)
    runpy.run_path(str(Path(config.__file__)), run_name="__main__")
    output = capsys.readouterr().out
    for name, value in values.items():
        assert value not in output
        assert f"{name}: configured" in output


def _artifact(tmp_path):
    return Artifact("1", "source", "", "v2:r", "insights", str(tmp_path / "out.json"))


@pytest.mark.parametrize("cancel", [False, True])
def test_partial_or_cancelled_artifact_write_preserves_old_bytes(tmp_path, cancel):
    artifact = _artifact(tmp_path)
    artifact_store.write_text(artifact, '{"old":true}')
    cancelled = threading.Event()

    def bad_writer(path):
        path.write_text('{"partial":')
        if cancel:
            cancelled.set()
        else:
            raise OSError("disk full")

    with pytest.raises(OperationCancelled if cancel else OSError):
        artifact_store.commit(artifact, bad_writer, is_cancelled=cancelled.is_set)
    assert Path(artifact.path).read_text() == '{"old":true}'
    assert artifact_store.is_cache_valid(artifact.path, artifact)
    assert not list(tmp_path.glob(".out.*"))


def test_crash_between_output_and_manifest_is_a_cache_miss(tmp_path, monkeypatch):
    artifact = _artifact(tmp_path)
    artifact_store.write_text(artifact, '{"old":true}')

    def fail(*args, **kwargs):
        raise OSError("manifest replacement failed")

    monkeypatch.setattr(artifact_store, "save", fail)
    with pytest.raises(OSError):
        artifact_store.write_text(artifact, '{"new":true}')
    assert not artifact_store.is_cache_valid(artifact.path, artifact)
    assert json.loads(Path(artifact.path).read_text()) == {"new": True}


def test_corrupt_or_legacy_artifact_cannot_be_reused(tmp_path):
    artifact = _artifact(tmp_path)
    artifact_store.write_text(artifact, '{"valid":true}')
    Path(artifact.path).write_text('{broken')
    assert not artifact_store.is_cache_valid(artifact.path, artifact)
    manifest = artifact_store.manifest_path_for(artifact.path)
    manifest.write_text(json.dumps(artifact.to_dict()))
    assert not artifact_store.is_cache_valid(artifact.path, artifact)


@pytest.mark.parametrize("changed", ["model", "endpoint"])
def test_memory_cache_separates_model_and_endpoint(tmp_path, monkeypatch, changed):
    from core.insights import generate_insight
    from core.insights_cache import InsightsCache

    calls = []

    class Client:
        def __init__(self, ps):
            self.ps = ps

        def chat_completion_stream(self, **kwargs):
            calls.append((self.ps.model, self.ps.base_url))
            return '[{"start":0,"title":"Synthetic"}]'

    monkeypatch.setattr("core.ai_provider.create_client", Client)
    cache = InsightsCache()
    old = ProviderSettings("openai", base_url="https://first.invalid/v1", model="model-a")
    new = dataclasses.replace(old, **({"model": "model-b"} if changed == "model" else {"base_url": "https://second.invalid/v1"}))
    for provider in (old, old, new):
        generate_insight("chapters", _result().segments, lm_url="", provider=provider, cache=cache)
    assert len(calls) == 2


def test_input_edit_during_generation_cannot_be_labelled_as_the_new_input(tmp_path, monkeypatch):
    context = _context(tmp_path)
    original = STEP_REGISTRY["insights"].make_artifact(context)

    def edit_during_call(*args, **kwargs):
        context.result.segments[0].text = "edited while generating"
        return []

    monkeypatch.setattr("core.insights.generate_insight", edit_during_call)
    STEP_REGISTRY["insights"].make_runner(context)()
    saved = artifact_store.load(tmp_path / "insights.json")
    assert saved.transcript_revision == original.transcript_revision
    assert not artifact_store.is_cache_valid(saved.path, STEP_REGISTRY["insights"].make_artifact(context))
