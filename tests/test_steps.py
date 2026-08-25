"""Unit tests for application/steps.py (see
docs/UI_REDESIGN_PLAN_2026-09.ru.md, B0).

Runs under system python with the stubbed PyQt6 (tests/conftest.py) —
every engine call is mocked via monkeypatch, so no real LM Studio, no
real Qt painting, and no real whisper/pyannote involvement.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from application.steps import (
    STEP_DEFINITIONS,
    STEP_REGISTRY,
    StepContext,
    build_cache_checks,
    build_job_spec,
    build_runners,
)
from application.job_engine import JobEngine
from domain.job import StepStatus
from domain.transcription import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[
            Segment(start=0.0, end=2.0, text="Hello world", speaker=None),
            Segment(start=2.0, end=5.0, text="Second segment", speaker=None),
        ],
        language="en",
        duration=5.0,
    )


def _context(tmp_path, **params) -> StepContext:
    return StepContext(
        source_path=str(tmp_path / "source.mp3"),
        result=_result(),
        record_id=1,
        artifact_dir=tmp_path / "artifacts",
        params=params,
    )


# ------------------------------------------------------------------ registry shape

def test_registry_covers_the_first_step_set():
    assert set(STEP_REGISTRY) == {
        "transcribe", "diarize", "clean", "article",
        "insights", "youtube_package", "book", "cover",
    }


def test_dependency_graph_matches_the_plan():
    deps = {step.name: set(step.depends_on) for step in STEP_DEFINITIONS}
    assert deps["transcribe"] == set()
    assert deps["diarize"] == {"transcribe"}
    assert deps["clean"] == {"transcribe"}
    assert deps["article"] == {"clean"}
    assert deps["insights"] == {"transcribe"}
    assert deps["youtube_package"] == {"transcribe"}
    assert deps["book"] == {"clean"}
    assert deps["cover"] == {"transcribe"}


@pytest.mark.parametrize(
    "step_names",
    [
        ("transcribe",),
        ("transcribe", "clean"),
        ("transcribe", "clean", "article"),
        ("transcribe", "clean", "youtube_package", "cover"),
        tuple(STEP_REGISTRY),
    ],
)
def test_build_job_spec_gives_a_valid_deterministic_order(step_names):
    spec = build_job_spec("test", step_names)
    order = spec.topological_order()
    assert set(order) == set(step_names)
    # Every dependency precedes its dependent.
    position = {name: i for i, name in enumerate(order)}
    for step in spec.steps:
        for dep in step.depends_on:
            assert position[dep] < position[step.name]
    # Deterministic: running it again gives the same order.
    assert build_job_spec("test", step_names).topological_order() == order


def test_build_job_spec_drops_dependencies_outside_the_requested_subset():
    """article depends on clean, but a caller running article alone
    (clean already satisfied some other way) must not choke on a
    dependency edge to a step that isn't in this JobSpec at all."""
    spec = build_job_spec("test", ("article",))
    assert spec.steps == (spec.steps[0],)
    assert spec.steps[0].depends_on == ()


# ------------------------------------------------------------------ make_artifact

_FILE_PRODUCING_STEPS = [
    name for name in STEP_REGISTRY if name not in ("transcribe", "diarize")
]


@pytest.mark.parametrize("step_name", _FILE_PRODUCING_STEPS)
def test_make_artifact_never_leaves_cache_key_fields_empty(tmp_path, step_name):
    context = _context(tmp_path, provider=None, model="test-model")
    artifact = STEP_REGISTRY[step_name].make_artifact(context)
    assert artifact is not None
    assert artifact.type
    assert artifact.path
    assert artifact.provider, f"{step_name}: provider must not be empty"
    assert artifact.model, f"{step_name}: model must not be empty"
    assert artifact.prompt_version, f"{step_name}: prompt_version must not be empty"


@pytest.mark.parametrize("step_name", ["transcribe", "diarize"])
def test_transcribe_and_diarize_have_no_artifact(tmp_path, step_name):
    context = _context(tmp_path)
    assert STEP_REGISTRY[step_name].make_artifact(context) is None


def test_make_artifact_is_deterministic_given_the_same_context(tmp_path):
    context = _context(tmp_path, provider=None, model="m")
    first = STEP_REGISTRY["article"].make_artifact(context)
    second = STEP_REGISTRY["article"].make_artifact(context)
    assert first.cache_key() == second.cache_key()
    assert first.path == second.path


def test_make_artifact_path_is_independent_of_generated_content(tmp_path):
    """The article step's expected path must be knowable *before* running
    — it can't depend on an LLM-generated title the way the interactive
    Export flow's filenames do (see application/steps.py's module
    docstring)."""
    context = _context(tmp_path, provider=None, model="m")
    artifact = STEP_REGISTRY["article"].make_artifact(context)
    assert artifact.path == str(context.artifact_dir / "articles.json")


# ------------------------------------------------------------------ runners (mocked engines)

def test_clean_runner_calls_text_processor_and_writes_output(tmp_path, monkeypatch):
    from text_processor import CleanedText, CoherentText, ProcessingResult

    calls = {}

    class _FakeTextProcessor:
        def __init__(self, *_args, **_kwargs):
            pass

        def process(self, raw_text, use_ai=True, on_progress=None):
            calls["raw_text"] = raw_text
            calls["use_ai"] = use_ai
            return ProcessingResult(
                original=raw_text,
                cleaned=CleanedText(raw_text, raw_text, 0, 0, 0),
                coherent=CoherentText("Cleaned and coherent text."),
            )

    monkeypatch.setattr("text_processor.TextProcessor", _FakeTextProcessor)

    context = _context(tmp_path, provider=None, model="m")
    runner = STEP_REGISTRY["clean"].make_runner(context)
    result = runner()

    assert calls["raw_text"] == context.result.full_text
    assert result.coherent.text == "Cleaned and coherent text."
    written = context.artifact_dir / "clean.md"
    assert written.read_text(encoding="utf-8") == "Cleaned and coherent text."
    assert written.with_suffix(".md.manifest.json").exists()


def test_article_runner_reads_clean_step_output(tmp_path, monkeypatch):
    from article_generator import Article, ArticleFormat, GenerationResult, TopicAnalysis
    from text_processor import CleanedText, CoherentText, ProcessingResult

    seen_text = {}

    class _FakeArticleGenerator:
        def __init__(self, *_args, **_kwargs):
            pass

        def generate_all_formats(self, text, formats=None, on_progress=None):
            seen_text["text"] = text
            articles = [
                Article(title=f"Title {fmt.value}", format=fmt, content=f"Body {fmt.value}")
                for fmt in (formats or list(ArticleFormat))
            ]
            return GenerationResult(
                source_text=text, topic_analysis=TopicAnalysis(), articles=articles,
            )

    monkeypatch.setattr("article_generator.ArticleGenerator", _FakeArticleGenerator)

    cleaned = ProcessingResult(
        original="raw", cleaned=CleanedText("raw", "raw", 0, 0, 0),
        coherent=CoherentText("Cleaned text for the article step."),
    )
    context = _context(
        tmp_path, provider=None, model="m",
        article_formats=[ArticleFormat.BLOG_POST, ArticleFormat.FAQ],
    )
    context = StepContext(
        source_path=context.source_path, result=context.result, record_id=context.record_id,
        artifact_dir=context.artifact_dir, params=context.params,
        get_result=lambda name: cleaned if name == "clean" else None,
    )
    runner = STEP_REGISTRY["article"].make_runner(context)
    result = runner()

    assert seen_text["text"] == "Cleaned text for the article step."
    assert {a.format for a in result.articles} == {ArticleFormat.BLOG_POST, ArticleFormat.FAQ}
    payload = json.loads((context.artifact_dir / "articles.json").read_text(encoding="utf-8"))
    assert set(payload) == {"blog", "faq"}
    assert payload["blog"]["content"] == "Body blog"


def test_article_runner_falls_back_to_full_text_without_a_clean_result(tmp_path, monkeypatch):
    from article_generator import Article, ArticleFormat, GenerationResult, TopicAnalysis

    seen_text = {}

    class _FakeArticleGenerator:
        def __init__(self, *_args, **_kwargs):
            pass

        def generate_all_formats(self, text, formats=None, on_progress=None):
            seen_text["text"] = text
            return GenerationResult(
                source_text=text, topic_analysis=TopicAnalysis(),
                articles=[Article(title="T", format=ArticleFormat.SUMMARY, content="C")],
            )

    monkeypatch.setattr("article_generator.ArticleGenerator", _FakeArticleGenerator)

    context = _context(tmp_path, provider=None, model="m")
    runner = STEP_REGISTRY["article"].make_runner(context)
    runner()

    assert seen_text["text"] == context.result.full_text


def test_insights_runner_calls_generate_insight_for_each_type(tmp_path, monkeypatch):
    calls = []

    def _fake_generate_insight(insight_type, segments, **kwargs):
        calls.append(insight_type)
        return [{"start": 0, "title": insight_type}]

    monkeypatch.setattr("core.insights.generate_insight", _fake_generate_insight)

    context = _context(tmp_path, provider=None, model="m", language="en")
    runner = STEP_REGISTRY["insights"].make_runner(context)
    result = runner()

    assert calls == ["chapters", "action_items", "key_moments"]
    assert set(result) == {"chapters", "action_items", "key_moments"}
    payload = json.loads(
        (context.artifact_dir / "insights.json").read_text(encoding="utf-8")
    )
    assert payload["chapters"][0]["title"] == "chapters"


def test_youtube_package_runner_calls_generate_insight_for_each_type(tmp_path, monkeypatch):
    calls = []

    def _fake_generate_insight(insight_type, segments, **kwargs):
        calls.append(insight_type)
        return [{"title": insight_type}]

    monkeypatch.setattr("core.insights.generate_insight", _fake_generate_insight)

    context = _context(tmp_path, provider=None, model="m")
    runner = STEP_REGISTRY["youtube_package"].make_runner(context)
    result = runner()

    assert calls == ["chapters", "yt_titles", "yt_description", "yt_tags", "yt_questions"]
    assert set(result) == set(calls)
    assert (context.artifact_dir / "youtube_package.json").exists()


def test_book_runner_calls_book_pipeline_and_writes_final_text(tmp_path, monkeypatch):
    from book_pipeline import BookResult, BookStageResult

    calls = {}

    class _FakeBookPipeline:
        def __init__(self, *_args, **_kwargs):
            pass

        def process(self, transcript_text, source_path, **kwargs):
            calls["transcript_text"] = transcript_text
            calls["kwargs"] = kwargs
            return BookResult(
                source_path=source_path,
                stages=[BookStageResult(stage="unwrap", output_text="Final book text.",
                                         output_path=str(tmp_path / "book_unwrap.md"))],
            )

    monkeypatch.setattr("book_pipeline.BookPipeline", _FakeBookPipeline)

    context = _context(tmp_path, provider=None, model="m")
    runner = STEP_REGISTRY["book"].make_runner(context)
    result = runner()

    assert calls["transcript_text"] == context.result.full_text
    assert "record_id" not in calls["kwargs"]  # see application/steps.py's comment
    assert result.final_text == "Final book text."
    written = context.artifact_dir / "book.md"
    assert written.read_text(encoding="utf-8") == "Final book text."


def test_cover_runner_calls_renderer_and_saves_image(tmp_path, monkeypatch):
    # covers.renderer imports real PyQt6 QPointF/QRectF/QSize that the
    # PyQt6 stand-ins in tests/conftest.py don't provide, so — unlike the
    # other engines above — it can't be imported at all under system
    # python, even just to monkeypatch an attribute on it. Replace the
    # whole module in sys.modules before the runner's lazy `from
    # covers.renderer import render` ever executes; covers.template (also
    # imported lazily) needs no such stand-in, it's already Qt-free.
    calls = {}

    class _FakeImage:
        def save(self, path, fmt):
            calls["saved_path"] = path
            calls["fmt"] = fmt
            from pathlib import Path
            Path(path).write_bytes(b"fake-png")
            return True

    def _fake_render(template, layout, variant, slots, size):
        calls["layout"] = layout
        calls["variant"] = variant
        return _FakeImage(), ["a warning"]

    renderer_stub = types.ModuleType("covers.renderer")
    renderer_stub.render = _fake_render
    monkeypatch.setitem(sys.modules, "covers.renderer", renderer_stub)
    monkeypatch.setattr("covers.template.load_template", lambda name: object())

    context = _context(tmp_path, provider=None, model="m", cover_layout="duo")
    runner = STEP_REGISTRY["cover"].make_runner(context)
    result = runner()

    assert calls["layout"] == "duo"
    assert result["warnings"] == ["a warning"]
    written = context.artifact_dir / "cover.png"
    assert written.exists()


# ------------------------------------------------------------------ end-to-end via JobEngine

def test_build_runners_and_cache_checks_work_with_job_engine(tmp_path, monkeypatch):
    """The registry's outputs plug directly into JobEngine — this is what
    application/job_runner.py (Track B, B1) will do off a QThread."""
    from text_processor import CleanedText, CoherentText, ProcessingResult

    class _FakeTextProcessor:
        def __init__(self, *_args, **_kwargs):
            pass

        def process(self, raw_text, use_ai=True, on_progress=None):
            return ProcessingResult(
                original=raw_text, cleaned=CleanedText(raw_text, raw_text, 0, 0, 0),
                coherent=CoherentText("Cleaned."),
            )

    monkeypatch.setattr("text_processor.TextProcessor", _FakeTextProcessor)

    context = _context(tmp_path, provider=None, model="m")
    step_names = ("transcribe", "clean")
    spec = build_job_spec("test", step_names)
    runners = build_runners(context, step_names)
    cache_checks = build_cache_checks(context, step_names)

    engine = JobEngine()
    run = engine.run(spec, runners, cache_checks=cache_checks)
    assert run.outcomes["transcribe"].status == StepStatus.SUCCEEDED
    assert run.outcomes["clean"].status == StepStatus.SUCCEEDED

    # Second run against the same artifact_dir must skip clean — the
    # manifest written by the first run now matches make_artifact().
    run2 = engine.run(spec, build_runners(context, step_names),
                       cache_checks=build_cache_checks(context, step_names))
    assert run2.outcomes["clean"].status == StepStatus.SKIPPED
