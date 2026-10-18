"""Can the selected recipe run right now? (S1, docs/archive/UI_CONCEPT_IMPLEMENTATION_PLAN_2026-10.ru.md)

Qt-free: the start screen gathers the facts (model file present, ffmpeg
found, LM Studio answering — from the status bar's existing probe, not
a second one — speaker model configured) and renders what this returns.
Each check that isn't satisfied names the place that fixes it.

Only a missing requirement of transcription itself (ffmpeg) blocks a
launch. A missing LM Studio or cloud key fails just the steps that need
it: those are listed as excluded, with an explicit "transcript only"
alternative, and the run screen offers to retry them later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from domain.recipe import effective_provider

OK = "ok"
WARN = "warn"            # will work, with a caveat (e.g. downloads first)
MISSING = "missing"      # the step that needs it will fail
CHECKING = "checking"    # not known yet

# Where a fix lives; the start screen maps these to actions.
FIX_SETTINGS_AI = "settings_ai"
FIX_SETTINGS_DIARIZATION = "settings_diarization"
FIX_SETTINGS_TRANSCRIPTION = "settings_transcription"
FIX_FFMPEG = "ffmpeg"
# Explicitly switch to the transcript-only recipe — offered when some of
# the selected recipe's steps cannot run, never applied silently.
FIX_TRANSCRIPT_ONLY = "transcript_only"

_CLOUD_LABELS = {"openai": "OpenAI", "anthropic": "Anthropic"}


@dataclass(frozen=True)
class ReadinessCheck:
    key: str                      # "model" | "ffmpeg" | "diarize" | "llm" | "llm_book" | "llm_cloud" | "partial"
    state: str                    # OK | WARN | MISSING | CHECKING
    text_key: str                 # i18n key
    params: dict = field(default_factory=dict)
    fix: str = ""                 # FIX_* or "" when nothing to do
    # The recipe steps this check is about; the start screen names them
    # ("Cleanup, Article — LM Studio isn't answering"). Empty for checks
    # that concern the whole run (model, ffmpeg).
    steps: tuple[str, ...] = ()
    # True when nothing can run at all (transcription itself would fail):
    # Launch is disabled rather than starting a run that only fails.
    blocking: bool = False


@dataclass(frozen=True)
class ReadinessFacts:
    model_label: str
    model_downloaded: bool
    ffmpeg_found: bool
    llm_provider: str = "lmstudio"          # Config.yt_provider
    llm_reachable: Optional[bool] = None    # None: not probed yet
    # A key string is present — not proof it authorizes anything: readiness
    # never makes a (possibly paid) request to verify it.
    cloud_key_set: bool = False
    diarize_ready: bool = False
    # Config.book_lm_url points somewhere other than lm_studio_url, so the
    # status bar's probe says nothing about it.
    book_endpoint_separate: bool = False


def recipe_checks(
    steps: Sequence[str], llm_steps: Sequence[str], facts: ReadinessFacts,
) -> list[ReadinessCheck]:
    """The checks that matter for a recipe running *steps*; *llm_steps*
    are the ones that need a language model (resource ``local_llm``).

    LLM requirements are grouped by who actually runs each step
    (``domain.recipe.effective_provider``): the configured cloud provider
    only ever runs the cloud-capable steps; everything else needs LM
    Studio, whatever the provider setting says (UX-02)."""
    checks: list[ReadinessCheck] = []
    if facts.model_downloaded:
        checks.append(ReadinessCheck("model", OK, "ready_model_ok", {"model": facts.model_label}))
    else:
        checks.append(ReadinessCheck(
            "model", WARN, "ready_model_download", {"model": facts.model_label},
            FIX_SETTINGS_TRANSCRIPTION,
        ))

    if facts.ffmpeg_found:
        checks.append(ReadinessCheck("ffmpeg", OK, "ready_ffmpeg_ok"))
    else:
        checks.append(ReadinessCheck(
            "ffmpeg", MISSING, "ready_ffmpeg_missing", fix=FIX_FFMPEG, blocking=True,
        ))

    if "diarize" in steps:
        if facts.diarize_ready:
            checks.append(ReadinessCheck("diarize", OK, "ready_diarize_ok", steps=("diarize",)))
        else:
            checks.append(ReadinessCheck(
                "diarize", WARN, "ready_diarize_missing", fix=FIX_SETTINGS_DIARIZATION,
                steps=("diarize",),
            ))

    used = [step for step in steps if step in llm_steps]
    cloud_steps = tuple(
        s for s in used if effective_provider(s, facts.llm_provider) != "lmstudio"
    )
    book_apart = tuple(s for s in used if s == "book" and facts.book_endpoint_separate)
    local_steps = tuple(s for s in used if s not in cloud_steps and s not in book_apart)

    if local_steps:
        if facts.llm_reachable is None:
            checks.append(ReadinessCheck(
                "llm", CHECKING, "ready_llm_checking", steps=local_steps))
        elif facts.llm_reachable:
            checks.append(ReadinessCheck("llm", OK, "ready_llm_ok", steps=local_steps))
        else:
            checks.append(ReadinessCheck(
                "llm", MISSING, "ready_llm_missing", fix=FIX_SETTINGS_AI, steps=local_steps,
            ))
    if book_apart:
        checks.append(ReadinessCheck(
            "llm_book", WARN, "ready_book_endpoint_unchecked", steps=book_apart,
        ))
    if cloud_steps:
        cloud = _CLOUD_LABELS.get(facts.llm_provider, facts.llm_provider)
        if facts.cloud_key_set:
            checks.append(ReadinessCheck(
                "llm_cloud", WARN, "ready_cloud_key_unverified", {"provider": cloud},
                steps=cloud_steps,
            ))
        else:
            checks.append(ReadinessCheck(
                "llm_cloud", MISSING, "ready_cloud_no_key", {"provider": cloud},
                FIX_SETTINGS_AI, steps=cloud_steps,
            ))

    excluded = excluded_steps(checks)
    if excluded and not launch_blocked(checks):
        checks.append(ReadinessCheck(
            "partial", WARN, "ready_partial", fix=FIX_TRANSCRIPT_ONLY, steps=excluded,
        ))
    return checks


def excluded_steps(checks: Sequence[ReadinessCheck]) -> tuple[str, ...]:
    """Steps a run would lose to a MISSING requirement, in check order."""
    seen: list[str] = []
    for check in checks:
        if check.state == MISSING:
            seen.extend(step for step in check.steps if step not in seen)
    return tuple(seen)


def launch_blocked(checks: Sequence[ReadinessCheck]) -> bool:
    """Nothing can run: a requirement of transcription itself is missing."""
    return any(check.blocking and check.state == MISSING for check in checks)
