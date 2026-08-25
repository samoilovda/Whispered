"""Step registry: what running a named step actually means.

``application/job_engine.py`` already knows how to run a ``JobSpec``'s
steps in dependency order, with per-resource concurrency limits and
cache-skip (see its docstring) — what it doesn't know is what "run the
youtube_package step" or "run the cover step" actually calls. That's this
module (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B0).

``domain.job.StepSpec.StepRunner`` takes no arguments, because
``JobEngine`` builds the whole ``runners`` dict once, up front, before any
step has resolved. ``make_runner(context)`` is called at that same time
and closes over a ``StepContext`` built for this one job run; the zero-arg
closure it returns is what actually executes later, once the step's
dependencies have succeeded.

Deliberately does not rewrite any of the engines it wraps —
``ArticleGenerator``, ``TextProcessor``, ``BookPipeline``,
``core.insights.generate_insight``, and the cover renderer are called
exactly as they already are. Migrating each UI panel onto these steps
(dropping its own trigger button/progress bar/worker) is Track B's B5
phase, one generator per commit — this module is what B5 points those
panels at.

Every step writes its output to a **deterministic** path under
``StepContext.artifact_dir`` (``clean.md``, ``articles.json``, ...),
independent of generated content (an LLM-produced article title, for
instance). The interactive per-panel "Export" actions elsewhere in the
app use their own versioned/title-derived filenames and are untouched —
those are a human choosing where their file goes; a job step needs a
name it can predict *before* running, so ``make_artifact()`` can describe
what a cache-valid rerun would look like.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from application.artifact_provenance import (
    source_fingerprint,
    transcript_revision as _transcript_revision,
)
from core.prompts import prompt_version
from domain.artifact import Artifact
from domain.job import JobSpec, StepSpec
from domain.transcription import TranscriptionResult

StepRunner = Callable[[], object]


@dataclass(frozen=True)
class StepContext:
    """Everything a step's runner/artifact-factory needs, gathered once
    before a job starts. Qt-free — no PyQt import, no widget reference
    (see CLAUDE.md's domain/application layering).

    ``params`` carries recipe- and run-level configuration a step's
    runner pulls specific keys from (documented per step below): ``lm_url``,
    ``provider`` (a ``core.ai_provider.ProviderSettings`` or ``None`` for
    LM Studio), ``model`` (LM Studio's loaded model name, when known —
    cloud providers carry their own on ``provider.model``), ``language``,
    and step-specific options (``use_ai``, ``article_formats``,
    ``do_unwrap``/``do_custom``/``custom_prompt_path``, cover
    ``layout``/``variant``/``slots``/``template``).

    ``get_result`` looks up an already-finished dependency's return value
    by step name (e.g. the ``article`` step reading ``clean``'s output) —
    it's a callable rather than a plain dict because it's bound to the
    live ``JobRun`` being executed (see ``application/job_runner.py``,
    Track B's B1), which only has entries for steps that have resolved
    *so far* at the time this step's runner actually calls it.
    """

    source_path: str
    result: TranscriptionResult
    record_id: "str | int"
    artifact_dir: Path
    params: dict = field(default_factory=dict)
    get_result: Callable[[str], Any] = field(default=lambda name: None)
    on_progress: Optional[Callable[[int, str], None]] = None
    is_cancelled: Callable[[], bool] = field(default=lambda: False)

    def source_hash(self) -> str:
        return source_fingerprint(self.source_path)

    def revision(self) -> str:
        return _transcript_revision(self.result.segments, self.result.language)

    def record_id_str(self) -> str:
        return str(self.record_id) if self.record_id is not None else "unsaved"


@dataclass(frozen=True)
class StepDefinition:
    """One entry in the step registry."""

    name: str
    label_key: str            # i18n key, not display text
    resource: str              # "default" | "local_llm" | "ffmpeg"
    depends_on: tuple
    viewer: str                 # which panel shows this step's result
    make_runner: Callable[[StepContext], StepRunner]
    make_artifact: Callable[[StepContext], Optional[Artifact]]


# ------------------------------------------------------------------ helpers

def _composite_prompt_version(*names: str) -> str:
    """Combine multiple prompt files' versions into one.

    A step that reads more than one ``prompts/*.md`` file (e.g. ``clean``
    uses both ``cleaning.md`` and ``coherence.md``) must invalidate its
    cache if *either* changes.
    """
    joined = "|".join(prompt_version(name) for name in names)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _provider_label(context: StepContext) -> str:
    provider = context.params.get("provider")
    return getattr(provider, "kind", None) or "lmstudio"


def _model_label(context: StepContext) -> str:
    provider = context.params.get("provider")
    provider_model = getattr(provider, "model", "") if provider is not None else ""
    if provider_model:
        return str(provider_model)
    return str(context.params.get("model", "") or "")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_artifact(context: StepContext, artifact: Artifact) -> None:
    """Best-effort provenance write — never turn a successful step into a
    reported failure just because the manifest couldn't be written (same
    posture as every existing provenance call site in this codebase)."""
    try:
        from infrastructure.persistence import artifact_store

        artifact_store.save(artifact)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        from core.logger import get_logger

        get_logger(__name__).warning(
            "Failed to write %s artifact manifest for %s: %s",
            artifact.type, artifact.path, exc,
        )


# ---------------------------------------------------------------- transcribe

def _transcribe_runner(context: StepContext) -> StepRunner:
    def run() -> TranscriptionResult:
        # Transcription runs before a job starts — by the time a
        # transcribe step would actually resolve, its result is already
        # sitting on context.result, and the caller (application/
        # job_runner.py, B1) has normally already marked it SKIPPED via a
        # cache hit against the history record itself (see
        # docs/UI_REDESIGN_PLAN_2026-09.ru.md, B7 — Live capture skips
        # this step the same way). This is a defensive pass-through for
        # the case nothing pre-seeded it, not a re-transcription.
        return context.result

    return run


def _transcribe_artifact(context: StepContext) -> Optional[Artifact]:
    # The transcript's home is the history DB record, not a generated
    # file this registry tracks — nothing to cache-check here.
    return None


# ---------------------------------------------------------------- diarize

def _diarize_runner(context: StepContext) -> StepRunner:
    def run() -> TranscriptionResult:
        # Diarization currently runs as part of the transcription pass
        # itself (Config.diarization_enabled), not as a separately
        # re-invoked step — same pass-through as transcribe until a real
        # standalone diarize runner is wired in.
        return context.result

    return run


def _diarize_artifact(context: StepContext) -> Optional[Artifact]:
    return None


# ---------------------------------------------------------------- clean

def _clean_runner(context: StepContext) -> StepRunner:
    def run():
        from text_processor import TextProcessor

        lm_url = context.params.get("lm_url")
        processor = TextProcessor(lm_url) if lm_url else TextProcessor()
        use_ai = bool(context.params.get("use_ai", True))
        result = processor.process(
            context.result.full_text, use_ai=use_ai, on_progress=context.on_progress,
        )
        path = context.artifact_dir / "clean.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.coherent.text, encoding="utf-8")
        _save_artifact(context, _clean_artifact(context))
        return result

    return run


def _clean_artifact(context: StepContext) -> Artifact:
    return Artifact(
        record_id=context.record_id_str(),
        source_hash=context.source_hash(),
        source_path=context.source_path,
        transcript_revision=context.revision(),
        type="cleaned_text",
        path=str(context.artifact_dir / "clean.md"),
        provider=_provider_label(context),
        model=_model_label(context),
        prompt_version=_composite_prompt_version("cleaning", "coherence"),
    )


# ---------------------------------------------------------------- article

def _article_runner(context: StepContext) -> StepRunner:
    def run():
        from article_generator import ArticleFormat, ArticleGenerator
        from core.lm_client import LMStudioClient

        cleaned = context.get_result("clean")
        text = cleaned.coherent.text if cleaned is not None else context.result.full_text

        lm_url = context.params.get("lm_url")
        client = LMStudioClient(lm_url) if lm_url else LMStudioClient()
        generator = ArticleGenerator(client)
        formats = context.params.get("article_formats") or list(ArticleFormat)
        result = generator.generate_all_formats(
            text, formats=formats, on_progress=context.on_progress
        )
        payload = {
            article.format.value: {"title": article.title, "content": article.content}
            for article in result.articles
        }
        _write_json(context.artifact_dir / "articles.json", payload)
        _save_artifact(context, _article_artifact(context))
        return result

    return run


_ARTICLE_PROMPT_NAMES = (
    "topic_extraction", "blog_post", "faq", "listicle", "summary", "social",
)


def _article_artifact(context: StepContext) -> Artifact:
    return Artifact(
        record_id=context.record_id_str(),
        source_hash=context.source_hash(),
        source_path=context.source_path,
        transcript_revision=context.revision(),
        type="article",
        path=str(context.artifact_dir / "articles.json"),
        provider=_provider_label(context),
        model=_model_label(context),
        prompt_version=_composite_prompt_version(*_ARTICLE_PROMPT_NAMES),
    )


# ---------------------------------------------------------------- insights

_INSIGHTS_TYPES = ("chapters", "action_items", "key_moments")


def _insights_runner(context: StepContext) -> StepRunner:
    def run():
        from core.insights import generate_insight

        lm_url = context.params.get("lm_url", "") or ""
        provider = context.params.get("provider")
        cache = context.params.get("insights_cache")
        language = context.params.get("language")

        payload: dict[str, Any] = {}
        for insight_type in _INSIGHTS_TYPES:
            payload[insight_type] = generate_insight(
                insight_type, context.result.segments,
                lm_url=lm_url, language=language, provider=provider, cache=cache,
                is_cancelled=context.is_cancelled,
            )
        _write_json(context.artifact_dir / "insights.json", payload)
        _save_artifact(context, _insights_artifact(context))
        return payload

    return run


def _insights_artifact(context: StepContext) -> Artifact:
    return Artifact(
        record_id=context.record_id_str(),
        source_hash=context.source_hash(),
        source_path=context.source_path,
        transcript_revision=context.revision(),
        type="insights",
        path=str(context.artifact_dir / "insights.json"),
        provider=_provider_label(context),
        model=_model_label(context),
        prompt_version=_composite_prompt_version(*_INSIGHTS_TYPES),
    )


# ---------------------------------------------------------------- youtube_package

_YOUTUBE_TYPES = ("chapters", "yt_titles", "yt_description", "yt_tags", "yt_questions")


def _youtube_package_runner(context: StepContext) -> StepRunner:
    def run():
        from core.insights import generate_insight

        lm_url = context.params.get("lm_url", "") or ""
        provider = context.params.get("provider")
        cache = context.params.get("insights_cache")
        language = context.params.get("language")

        payload: dict[str, Any] = {}
        for insight_type in _YOUTUBE_TYPES:
            payload[insight_type] = generate_insight(
                insight_type, context.result.segments,
                lm_url=lm_url, language=language, provider=provider, cache=cache,
                is_cancelled=context.is_cancelled,
            )
        _write_json(context.artifact_dir / "youtube_package.json", payload)
        _save_artifact(context, _youtube_package_artifact(context))
        return payload

    return run


def _youtube_package_artifact(context: StepContext) -> Artifact:
    return Artifact(
        record_id=context.record_id_str(),
        source_hash=context.source_hash(),
        source_path=context.source_path,
        transcript_revision=context.revision(),
        type="youtube_package",
        path=str(context.artifact_dir / "youtube_package.json"),
        provider=_provider_label(context),
        model=_model_label(context),
        prompt_version=_composite_prompt_version(*_YOUTUBE_TYPES),
    )


# ---------------------------------------------------------------- book

def _book_runner(context: StepContext) -> StepRunner:
    def run():
        from book_pipeline import BookPipeline

        pipeline = BookPipeline()
        do_unwrap = bool(context.params.get("do_unwrap", True))
        do_custom = bool(context.params.get("do_custom", False))
        custom_prompt_path = str(context.params.get("custom_prompt_path", "") or "")
        book_dir = context.artifact_dir / "book"
        result = pipeline.process(
            context.result.full_text,
            context.source_path,
            output_dir=book_dir,
            do_unwrap=do_unwrap,
            do_custom=do_custom,
            custom_prompt_path=custom_prompt_path,
            on_progress=context.on_progress,
            is_cancelled=context.is_cancelled,
            # record_id intentionally omitted: BookPipeline would write its
            # own provenance manifests for each versioned stage file with
            # provider/model/prompt_version left empty (see B0's trap #2 in
            # docs/UI_REDESIGN_PLAN_2026-09.ru.md, §2.4) — this step writes
            # one complete manifest itself, below, for the deterministic
            # wrapper file it controls instead.
        )
        path = context.artifact_dir / "book.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.final_text, encoding="utf-8")
        _save_artifact(context, _book_artifact(context))
        return result

    return run


def _book_artifact(context: StepContext) -> Artifact:
    return Artifact(
        record_id=context.record_id_str(),
        source_hash=context.source_hash(),
        source_path=context.source_path,
        transcript_revision=context.revision(),
        type="book",
        path=str(context.artifact_dir / "book.md"),
        provider=_provider_label(context),
        model=_model_label(context),
        prompt_version=prompt_version("расшивка"),
    )


# ---------------------------------------------------------------- cover

def _cover_runner(context: StepContext) -> StepRunner:
    def run():
        # Qt-dependent (QImage/QPainter) — imported here, not at module
        # level, so importing application.steps never requires a real (or
        # stubbed) PyQt6 install just to build the registry (see CLAUDE.md
        # rule 7 on lazy imports for heavy/optional deps).
        from covers.renderer import render
        from covers.template import load_template

        template_name = str(context.params.get("cover_template", "prosvet_16x9"))
        layout = str(context.params.get("cover_layout", "solo"))
        variant = str(context.params.get("cover_variant", "mint"))
        slots = dict(context.params.get("cover_slots") or {})
        size = context.params.get("cover_size", (1280, 720))

        template = load_template(template_name)
        image, warnings = render(template, layout, variant, slots, size)
        path = context.artifact_dir / "cover.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(path), "PNG"):
            raise RuntimeError(f"Failed to save cover image to {path}")
        _save_artifact(context, _cover_artifact(context))
        return {"path": str(path), "warnings": warnings}

    return run


def _cover_artifact(context: StepContext) -> Artifact:
    return Artifact(
        record_id=context.record_id_str(),
        source_hash=context.source_hash(),
        source_path=context.source_path,
        transcript_revision=context.revision(),
        type="cover",
        path=str(context.artifact_dir / "cover.png"),
        provider=_provider_label(context),
        model=_model_label(context),
        prompt_version=prompt_version("thumb_title"),
    )


# ------------------------------------------------------------------ registry

STEP_DEFINITIONS: tuple = (
    StepDefinition(
        name="transcribe",
        label_key="step_transcribe",
        resource="default",
        depends_on=(),
        viewer="transcript",
        make_runner=_transcribe_runner,
        make_artifact=_transcribe_artifact,
    ),
    StepDefinition(
        name="diarize",
        label_key="step_diarize",
        resource="default",
        depends_on=("transcribe",),
        viewer="transcript",
        make_runner=_diarize_runner,
        make_artifact=_diarize_artifact,
    ),
    StepDefinition(
        name="clean",
        label_key="step_clean",
        resource="local_llm",
        depends_on=("transcribe",),
        viewer="cleaned_text",
        make_runner=_clean_runner,
        make_artifact=_clean_artifact,
    ),
    StepDefinition(
        name="article",
        label_key="step_article",
        resource="local_llm",
        depends_on=("clean",),
        viewer="article",
        make_runner=_article_runner,
        make_artifact=_article_artifact,
    ),
    StepDefinition(
        name="insights",
        label_key="step_insights",
        resource="local_llm",
        depends_on=("transcribe",),
        viewer="insights",
        make_runner=_insights_runner,
        make_artifact=_insights_artifact,
    ),
    StepDefinition(
        name="youtube_package",
        label_key="step_youtube_package",
        resource="local_llm",
        depends_on=("transcribe",),
        viewer="youtube",
        make_runner=_youtube_package_runner,
        make_artifact=_youtube_package_artifact,
    ),
    StepDefinition(
        name="book",
        label_key="step_book",
        resource="local_llm",
        depends_on=("clean",),
        viewer="book",
        make_runner=_book_runner,
        make_artifact=_book_artifact,
    ),
    StepDefinition(
        name="cover",
        label_key="step_cover",
        resource="default",
        depends_on=("transcribe",),
        viewer="cover",
        make_runner=_cover_runner,
        make_artifact=_cover_artifact,
    ),
)

STEP_REGISTRY: dict = {step.name: step for step in STEP_DEFINITIONS}


def build_job_spec(name: str, step_names: "tuple[str, ...] | list") -> JobSpec:
    """Build a JobSpec for the given step names, pulling each step's
    resource/depends_on from the registry.

    Raises ``KeyError`` for an unknown step name — callers that need to
    tolerate unknown steps (e.g. a user-edited recipe read back from disk,
    see B2) must filter *step_names* against ``STEP_REGISTRY`` themselves
    first.
    """
    steps = tuple(
        StepSpec(
            name=step_name,
            resource=STEP_REGISTRY[step_name].resource,
            depends_on=tuple(
                dep for dep in STEP_REGISTRY[step_name].depends_on if dep in step_names
            ),
        )
        for step_name in step_names
    )
    return JobSpec(name=name, steps=steps)


def build_runners(
    context: StepContext,
    step_names: "tuple[str, ...] | list",
    *,
    progress_factory: "Optional[Callable[[str], Callable[[int, str], None]]]" = None,
) -> dict:
    """Build the ``name -> StepRunner`` mapping ``JobEngine.run()`` needs.

    *progress_factory*, when given, is called once per step name to get
    that step's own ``on_progress`` callback — a fresh :class:`StepContext`
    with it wired in is used for that step's runner instead of *context*
    unchanged. ``application/job_runner.py`` (Track B, B1) passes its
    ``make_progress_callback`` here so each step's progress reports
    arrive tagged with the right step name, without every step's runner
    otherwise needing to know its own name.
    """
    import dataclasses

    runners = {}
    for name in step_names:
        step_context = context
        if progress_factory is not None:
            step_context = dataclasses.replace(context, on_progress=progress_factory(name))
        runners[name] = STEP_REGISTRY[name].make_runner(step_context)
    return runners


def build_cache_checks(context: StepContext, step_names: "tuple[str, ...] | list") -> dict:
    """Cache-check callables for JobEngine.run(), one per step that has a
    real Artifact to check (transcribe/diarize return None — see above —
    and are simply never given an entry, matching JobEngine's contract
    that a missing cache_checks entry means "always run")."""
    from infrastructure.persistence import artifact_store

    checks = {}
    for name in step_names:
        expected = STEP_REGISTRY[name].make_artifact(context)
        if expected is None:
            continue
        checks[name] = lambda expected=expected: artifact_store.is_cache_valid(
            expected.path, expected
        )
    return checks
