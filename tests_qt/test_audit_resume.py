"""Real-Qt recovery after failed, cancelled, missing and damaged steps."""

from types import SimpleNamespace

import pytest

from application.job_engine import JobRun
from application.steps import STEP_REGISTRY, StepContext, build_job_spec
from application import run_store
from core.history import HistoryStore
from core.paths import artifact_dir
from domain.job import StepOutcome, StepStatus
from domain.transcription import Segment, TranscriptionResult
from infrastructure.persistence import artifact_store


@pytest.fixture
def recovery(monkeypatch, tmp_path, process_events):
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config(last_recipe="podcast_article"))
    store = HistoryStore(tmp_path / "history.db")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    result = TranscriptionResult([Segment(0, 5, "synthetic speech")], "en", 5)
    record = store.add(result, source_path="")
    calls = []

    class Processor:
        def __init__(self, *args, **kwargs):
            self.lm_client = SimpleNamespace(is_cancelled=None)

        def process(self, text, **kwargs):
            from text_processor import CleanedText, CoherentText, ProcessingResult

            calls.append("clean")
            return ProcessingResult(text, CleanedText(text, text, 0, 0, 0), CoherentText("new cleaned speech"))

    class Generator:
        def __init__(self, *args, **kwargs):
            self.lm_client = SimpleNamespace(is_cancelled=None)

        def generate_all_formats(self, text, **kwargs):
            from article_generator import Article, ArticleFormat, GenerationResult, TopicAnalysis

            calls.append(("article", text))
            return GenerationResult(text, TopicAnalysis(), [Article("Title", ArticleFormat.BLOG_POST, "Body")])

    monkeypatch.setattr("text_processor.TextProcessor", Processor)
    monkeypatch.setattr("article_generator.ArticleGenerator", Generator)
    from ui.main_window import MainWindow

    window = MainWindow()
    yield window, store, record, result, calls
    window.close()
    process_events()


@pytest.mark.parametrize("status", [StepStatus.FAILED, StepStatus.CANCELLED])
def test_continue_actually_reexecutes_failed_and_cancelled_steps(recovery, process_events, status):
    window, store, record, result, calls = recovery
    spec = build_job_spec("podcast_article", ("transcribe", "clean", "article"))
    run = JobRun(spec)
    run.outcomes = {
        "transcribe": StepOutcome("transcribe", StepStatus.SUCCEEDED),
        "clean": StepOutcome("clean", status, error="previous interruption"),
        "article": StepOutcome("article", StepStatus.CANCELLED),
    }
    run_store.save_run(record, "podcast_article", run, status="failed", db_path=store._db_path)
    window.recipe_run.resume(record)
    runner = window.recipe_run.job
    assert runner is not None and runner.wait(5000)
    process_events()
    assert calls == ["clean", ("article", "new cleaned speech")]
    assert run_store.load_latest_run(record, db_path=store._db_path).status == "done"


@pytest.mark.parametrize("damage", ["missing", "corrupt", "stale"])
def test_resume_does_not_trust_success_without_current_readable_artifact(recovery, process_events, damage):
    window, store, record, result, calls = recovery
    spec = build_job_spec("podcast_article", ("transcribe", "clean", "article"))
    run = JobRun(spec)
    run.outcomes = {s.name: StepOutcome(s.name, StepStatus.SUCCEEDED) for s in spec.steps}
    context = StepContext("", result, record, artifact_dir(record, "recording"), params={"model": "test-model"})
    clean = STEP_REGISTRY["clean"].make_artifact(context)
    artifact_store.write_text(clean, "old cleaned speech")
    if damage == "missing":
        (context.artifact_dir / "clean.md").unlink()
    elif damage == "corrupt":
        (context.artifact_dir / "clean.md").write_text("unverified replacement")
    else:
        manifest = artifact_store.manifest_path_for(clean.path)
        import json

        data = json.loads(manifest.read_text())
        data["transcript_revision"] = "old-revision"
        manifest.write_text(json.dumps(data))
    run_store.save_run(record, "podcast_article", run, status="interrupted", db_path=store._db_path)
    window.recipe_run.resume(record)
    runner = window.recipe_run.job
    assert runner is not None and runner.wait(5000)
    process_events()
    assert calls == ["clean", ("article", "new cleaned speech")]
