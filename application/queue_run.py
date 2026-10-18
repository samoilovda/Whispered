"""Running a queued file's recipe after its transcription (UX-14 / T14).

Qt-free: builds the same ``JobRun`` / ``StepContext`` a recipe run on the
open record would, but from the item's frozen snapshot and without touching
any window state. ``ui/queue_controller.py`` launches the JobRunner and
feeds the finished run back to ``finish_run()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from application.job_engine import JobRun
from application.steps import (
    build_job_spec, build_step_context, llm_params, load_step_result,
    prepare_resumed_run,
)
from domain.job import JobSpec, StepOutcome, StepStatus
from domain.queue import QueueItem
from domain.recipe import Recipe
from domain.transcription import TranscriptionResult


@dataclass
class FollowUp:
    spec: JobSpec
    run: JobRun
    context: Any            # StepContext
    step_names: tuple
    recipe_name: str
    run_id: Optional[int]   # an existing job_runs row being continued


def prepare_follow_up(
    item: QueueItem, result: TranscriptionResult, record_id: int,
    *, cfg: Any, provider: Any = None, insights_cache: Any = None,
) -> Optional[FollowUp]:
    """The recipe steps still to run for *item*, or ``None`` when its
    snapshot has none (a transcript-only recipe).

    Transcription is pre-resolved: the queue's own transcriber produced
    *result*. When the item already has a stored run (a retry or a resume
    after a restart) its finished steps are kept and only the rest run —
    ``prepare_resumed_run`` drops any whose saved output no longer fits.
    """
    from application import run_store

    if item.snapshot is None or not item.snapshot.follow_up_steps:
        return None
    recipe = Recipe.from_dict(item.snapshot.recipe)
    spec = recipe.to_job_spec(build_job_spec)
    step_names = tuple(step.name for step in spec.steps)
    run = JobRun(spec=spec)

    def get_result(name: str):
        outcome = run.outcomes.get(name)
        if outcome is None:
            return None
        return outcome.result if outcome.result is not None else load_step_result(context, name)

    context = build_step_context(
        item.source_path, result, record_id,
        artifact_source=item.source_path,
        params=llm_params(cfg, result, provider=provider, insights_cache=insights_cache,
                          profile=_queued_profile(item)),
        get_result=get_result, is_cancelled=run.is_cancelled,
    )
    stored = run_store.load_run(item.run_id) if item.run_id is not None else None
    if stored is not None:
        run_store.apply_stored_outcomes(run, stored)
        prepare_resumed_run(run, context)
    elif "transcribe" in step_names:
        run.outcomes["transcribe"] = StepOutcome("transcribe", StepStatus.SUCCEEDED, result=result)
    return FollowUp(spec, run, context, step_names, recipe.builtin_key or recipe.name,
                    stored.id if stored is not None else None)


def _queued_profile(item: QueueItem):
    """The profile the item was queued with; ``None`` = the plain settings
    (never today's active one)."""
    from domain.ai_profile import ProfileError, profile_from_dict

    data = item.snapshot.llm if item.snapshot is not None else {}
    if not data:
        return None
    try:
        return profile_from_dict(data)
    except ProfileError:
        return None


def finish_run(run: JobRun) -> tuple[str, str, tuple]:
    """(queue state, error text, artifact types) for a finished follow-up
    run: ``done`` / ``failed`` (with the first failure in the user's terms,
    secrets removed) / ``cancelled``."""
    from application.run_report import CANCELLED, FAILED, PARTIAL, SUCCESS, build_run_report

    report = build_run_report(run)
    if report.state == SUCCESS:
        return "done", "", report.materials
    if report.state == CANCELLED:
        return "cancelled", "", report.materials
    if report.state in (FAILED, PARTIAL) and report.failed:
        failure = report.failed[0]
        return "failed", f"{failure.step}:{failure.kind}", report.materials
    return "failed", report.state, report.materials
