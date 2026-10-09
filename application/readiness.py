"""Can the selected recipe run right now? (S1, docs/archive/UI_CONCEPT_IMPLEMENTATION_PLAN_2026-10.ru.md)

Qt-free: the start screen gathers the facts (model file present, ffmpeg
found, LM Studio answering — from the status bar's existing probe, not
a second one — speaker model configured) and renders what this returns.
Each check that isn't satisfied names the place that fixes it.

Nothing here blocks a launch: a missing LM Studio only fails the LLM
steps, which the run screen then offers to retry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

OK = "ok"
WARN = "warn"            # will work, with a caveat (e.g. downloads first)
MISSING = "missing"      # the step that needs it will fail
CHECKING = "checking"    # not known yet

# Where a fix lives; the start screen maps these to actions.
FIX_SETTINGS_AI = "settings_ai"
FIX_SETTINGS_DIARIZATION = "settings_diarization"
FIX_SETTINGS_TRANSCRIPTION = "settings_transcription"
FIX_FFMPEG = "ffmpeg"

_CLOUD_LABELS = {"openai": "OpenAI", "anthropic": "Anthropic"}


@dataclass(frozen=True)
class ReadinessCheck:
    key: str                      # "model" | "ffmpeg" | "llm" | "diarize"
    state: str                    # OK | WARN | MISSING | CHECKING
    text_key: str                 # i18n key
    params: dict = field(default_factory=dict)
    fix: str = ""                 # FIX_* or "" when nothing to do


@dataclass(frozen=True)
class ReadinessFacts:
    model_label: str
    model_downloaded: bool
    ffmpeg_found: bool
    llm_provider: str = "lmstudio"          # Config.yt_provider
    llm_reachable: Optional[bool] = None    # None: not probed yet
    cloud_key_set: bool = False
    diarize_ready: bool = False


def recipe_checks(
    steps: Sequence[str], llm_steps: Sequence[str], facts: ReadinessFacts,
) -> list[ReadinessCheck]:
    """The checks that matter for a recipe running *steps*; *llm_steps*
    are the ones that need a language model (resource ``local_llm``)."""
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
        checks.append(ReadinessCheck("ffmpeg", MISSING, "ready_ffmpeg_missing", fix=FIX_FFMPEG))

    if "diarize" in steps:
        if facts.diarize_ready:
            checks.append(ReadinessCheck("diarize", OK, "ready_diarize_ok"))
        else:
            checks.append(ReadinessCheck(
                "diarize", WARN, "ready_diarize_missing", fix=FIX_SETTINGS_DIARIZATION,
            ))

    if any(step in llm_steps for step in steps):
        cloud = _CLOUD_LABELS.get(facts.llm_provider)
        if cloud is not None:
            if facts.cloud_key_set:
                checks.append(ReadinessCheck("llm", OK, "ready_cloud_ok", {"provider": cloud}))
            else:
                checks.append(ReadinessCheck(
                    "llm", MISSING, "ready_cloud_no_key", {"provider": cloud}, FIX_SETTINGS_AI,
                ))
        elif facts.llm_reachable is None:
            checks.append(ReadinessCheck("llm", CHECKING, "ready_llm_checking"))
        elif facts.llm_reachable:
            checks.append(ReadinessCheck("llm", OK, "ready_llm_ok"))
        else:
            checks.append(ReadinessCheck("llm", MISSING, "ready_llm_missing", fix=FIX_SETTINGS_AI))
    return checks
