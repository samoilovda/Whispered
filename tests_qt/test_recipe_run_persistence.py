"""Real-Qt test for MainWindow's B8 wiring of a recipe launch into
application/run_store.py's job_runs table (see
docs/UI_REDESIGN_PLAN_2026-09.ru.md, B8) — the Library card's run
composition and the command palette's retriable steps both read this.
"""

from __future__ import annotations

from application.run_store import load_latest_run
from config import get_config
from core.history import HistoryStore
from transcriber import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello world")], language="en", duration=1.0,
    )


def test_a_finished_recipe_run_is_persisted_to_job_runs(monkeypatch, tmp_path, process_events):
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    get_config().last_recipe = "transcript_only"

    from ui.main_window import MainWindow

    window = MainWindow()
    window._document_session.apply_result(_result())
    window._last_record_id = store.add(_result(), source_path="", model="")

    window._run_recipe(_result())
    runner = window._recipe_job
    assert runner is not None
    assert runner.wait(2000)
    process_events()

    stored = load_latest_run(window._last_record_id)
    assert stored is not None
    assert stored.recipe == "transcript_only"
    assert stored.status == "done"
    assert stored.outcomes["transcribe"]["status"] == "succeeded"

    window.close()


# ------------------------------------------------------------------ B2: resuming an interrupted run

def test_resume_run_only_executes_the_missing_steps(monkeypatch, tmp_path, process_events):
    """B2, docs/IMPROVEMENT_PLAN_2026-08.ru.md: a run written directly to
    job_runs with only "transcribe"/"clean" recorded (as if the process
    died before "article" ever resolved) must, on resume, skip re-running
    clean (its real artifact is already on disk) and only actually
    execute article."""
    import config
    from application import run_store
    from application.job_engine import JobRun
    from application.steps import STEP_REGISTRY, build_job_spec, StepContext
    from core.history import HistoryStore
    from core.paths import artifact_dir
    from domain.job import StepOutcome, StepStatus

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config(last_recipe="podcast_article"))

    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    record_id = store.add(_result(), source_path="", model="")

    class _RealisticTextProcessor:
        def __init__(self, *_a, **_k):
            self.lm_client = type("C", (), {"is_cancelled": None})()

        def process(self, text, use_ai=True, on_progress=None):
            from text_processor import CleanedText, CoherentText, ProcessingResult

            return ProcessingResult(
                original=text,
                cleaned=CleanedText(text, text, 0, 0, 0),
                coherent=CoherentText("Cleaned before the crash."),
            )

    # Write clean's real artifact to disk directly through the step
    # registry (same pattern tests/test_steps.py uses) — the resumed run
    # needs a genuine clean.md for load_step_result() to rehydrate, both
    # for the article step's dependency and for the Cleaned tab.
    monkeypatch.setattr("text_processor.TextProcessor", _RealisticTextProcessor)
    out_dir = artifact_dir(record_id, "recording")
    setup_context = StepContext(
        source_path="", result=_result(), record_id=record_id,
        artifact_dir=out_dir, params={"lm_url": ""},
    )
    STEP_REGISTRY["clean"].make_runner(setup_context)()

    spec = build_job_spec("podcast_article", ("transcribe", "clean", "article"))
    partial_run = JobRun(spec=spec)
    partial_run.outcomes["transcribe"] = StepOutcome("transcribe", StepStatus.SUCCEEDED)
    partial_run.outcomes["clean"] = StepOutcome("clean", StepStatus.SUCCEEDED)
    run_store.save_run(record_id, "podcast_article", partial_run, status="interrupted")

    class _ExplodingTextProcessor(_RealisticTextProcessor):
        def process(self, text, use_ai=True, on_progress=None):
            raise AssertionError("resume must not re-run the already-succeeded clean step")

    article_calls = []

    class _FakeArticleGenerator:
        def __init__(self, *_a, **_k):
            self.lm_client = type("C", (), {"is_cancelled": None})()

        def generate_all_formats(self, text, formats=None, on_progress=None):
            from article_generator import Article, ArticleFormat, GenerationResult, TopicAnalysis

            article_calls.append(text)
            articles = [
                Article(title=f"Title {fmt.value}", format=fmt, content="body")
                for fmt in (formats or list(ArticleFormat))
            ]
            return GenerationResult(
                source_text=text, topic_analysis=TopicAnalysis(), articles=articles,
            )

    monkeypatch.setattr("text_processor.TextProcessor", _ExplodingTextProcessor)
    monkeypatch.setattr("article_generator.ArticleGenerator", _FakeArticleGenerator)

    from ui.main_window import MainWindow

    window = MainWindow()
    window._resume_run(record_id)
    # Captured immediately, before any process_events() — _launch_recipe_job()
    # sets self._recipe_job synchronously, but a fast-finishing run can
    # have job_finished (which resets it to None) already queued by the
    # time control returns here, and the very next process_events() call
    # would deliver it.
    runner = window._recipe_job
    assert runner is not None
    assert runner.wait(5000)
    process_events()

    assert len(article_calls) == 1
    assert article_calls[0] == "Cleaned before the crash."
    assert window._recipe_run.outcomes["clean"].status is StepStatus.SUCCEEDED
    assert window._recipe_run.outcomes["article"].status is StepStatus.SUCCEEDED
    assert window._cleaned_text == "Cleaned before the crash."
    assert window.article_view.has_articles()

    stored = load_latest_run(record_id)
    assert stored is not None
    assert stored.status == "done"
    assert stored.outcomes["article"]["status"] == "succeeded"

    window.close()
    process_events()


def test_resume_run_marks_a_dead_running_row_as_interrupted_first(
    monkeypatch, tmp_path, process_events,
):
    """A row still 'running' at app startup means the process that wrote
    it is dead (see run_store.mark_stale_running_as_interrupted's
    docstring) — MainWindow.__init__ must flip it before the Library
    ever reads job_runs, or a crashed run's card would never offer
    "Продолжить" at all."""
    import config
    from application import run_store
    from application.job_engine import JobRun
    from application.steps import build_job_spec
    from core.history import HistoryStore
    from domain.job import StepOutcome, StepStatus

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())

    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    record_id = store.add(_result(), source_path="", model="")

    spec = build_job_spec("transcript_only", ("transcribe",))
    run = JobRun(spec=spec)
    run.outcomes["transcribe"] = StepOutcome("transcribe", StepStatus.SUCCEEDED)
    run_store.save_run(record_id, "transcript_only", run, status="running")

    from ui.main_window import MainWindow

    window = MainWindow()
    process_events()

    stored = load_latest_run(record_id)
    assert stored is not None
    assert stored.status == "interrupted"

    window.close()
    process_events()
