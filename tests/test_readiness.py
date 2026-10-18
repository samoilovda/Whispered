"""Per-step readiness (UI/UX audit 2026-10-18, UX-02 / T02).

The check that used to say "AI: OpenAI" for Insights — a step that always
runs on LM Studio — is now grouped by who actually runs each step
(``domain.recipe.effective_provider``), the same rule the run uses.
"""

from __future__ import annotations

import itertools

import pytest

from application.readiness import (
    CHECKING, FIX_FFMPEG, FIX_SETTINGS_AI, FIX_TRANSCRIPT_ONLY, MISSING, OK, WARN, ReadinessFacts, excluded_steps,
    launch_blocked, recipe_checks,
)
from domain.recipe import BUILTIN_RECIPES_BY_KEY, effective_provider

LLM_STEPS = ("clean", "article", "insights", "youtube_package", "book")


def _facts(**overrides) -> ReadinessFacts:
    base = dict(model_label="turbo", model_downloaded=True, ffmpeg_found=True)
    base.update(overrides)
    return ReadinessFacts(**base)


def _by_key(checks):
    return {check.key: check for check in checks}


def test_transcript_only_needs_no_llm():
    checks = _by_key(recipe_checks(("transcribe",), LLM_STEPS, _facts()))
    assert set(checks) == {"model", "ffmpeg"}
    assert all(c.state == OK for c in checks.values())


def test_llm_state_follows_the_probe():
    steps = ("transcribe", "insights")
    assert _by_key(recipe_checks(steps, LLM_STEPS, _facts()))["llm"].state == CHECKING
    assert _by_key(recipe_checks(steps, LLM_STEPS, _facts(llm_reachable=True)))["llm"].state == OK
    missing = _by_key(recipe_checks(steps, LLM_STEPS, _facts(llm_reachable=False)))["llm"]
    assert missing.state == MISSING and missing.fix == FIX_SETTINGS_AI


def test_cloud_provider_needs_a_key_not_lm_studio():
    steps = ("transcribe", "youtube_package")
    checks = _by_key(recipe_checks(steps, LLM_STEPS, _facts(llm_provider="openai", llm_reachable=False)))
    assert "llm" not in checks
    check = checks["llm_cloud"]
    assert check.state == MISSING and check.params == {"provider": "OpenAI"}
    check = _by_key(recipe_checks(steps, LLM_STEPS, _facts(llm_provider="openai", cloud_key_set=True)))["llm_cloud"]
    # Key present, never verified by a request: a caveat, not a green tick.
    assert check.state == WARN


def test_missing_pieces_name_their_fix():
    checks = _by_key(recipe_checks(
        ("transcribe", "diarize"), LLM_STEPS,
        _facts(model_downloaded=False, ffmpeg_found=False, diarize_ready=False),
    ))
    assert checks["model"].state == WARN
    assert checks["ffmpeg"].state == MISSING and checks["ffmpeg"].fix == FIX_FFMPEG
    assert checks["diarize"].state == WARN and checks["diarize"].fix


def test_insights_with_cloud_key_and_lm_studio_off_is_not_ready():
    checks = _by_key(recipe_checks(
        ["transcribe", "insights"], LLM_STEPS,
        _facts(llm_provider="openai", cloud_key_set=True, llm_reachable=False),
    ))
    assert "llm_cloud" not in checks
    assert checks["llm"].state == MISSING
    assert checks["llm"].steps == ("insights",)


def test_mixed_youtube_recipe_reports_local_and_cloud_parts_separately():
    checks = _by_key(recipe_checks(
        ["transcribe", "clean", "youtube_package"], LLM_STEPS,
        _facts(llm_provider="openai", cloud_key_set=True, llm_reachable=False),
    ))
    assert checks["llm"].state == MISSING and checks["llm"].steps == ("clean",)
    cloud = checks["llm_cloud"]
    # A key string present is not a verified connection.
    assert cloud.state == WARN and cloud.text_key == "ready_cloud_key_unverified"
    assert cloud.steps == ("youtube_package",)
    assert checks["partial"].steps == ("clean",)
    assert checks["partial"].fix == FIX_TRANSCRIPT_ONLY


def test_missing_cloud_key_excludes_only_the_cloud_step():
    checks = recipe_checks(
        ["transcribe", "clean", "youtube_package"], LLM_STEPS,
        _facts(llm_provider="anthropic", cloud_key_set=False, llm_reachable=True),
    )
    assert excluded_steps(checks) == ("youtube_package",)
    assert _by_key(checks)["llm"].state == OK


def test_missing_ffmpeg_blocks_launch_and_offers_no_partial_run():
    checks = recipe_checks(["transcribe", "clean"], LLM_STEPS,
                           _facts(ffmpeg_found=False, llm_reachable=False))
    assert launch_blocked(checks)
    assert "partial" not in _by_key(checks)


def test_asr_checks_do_not_depend_on_the_llm():
    for reachable in (None, True, False):
        checks = _by_key(recipe_checks(["transcribe"], LLM_STEPS,
                                       _facts(llm_reachable=reachable)))
        assert set(checks) == {"model", "ffmpeg"}


def test_separate_book_endpoint_is_not_vouched_for_by_the_main_probe():
    checks = _by_key(recipe_checks(
        ["transcribe", "clean", "book"], LLM_STEPS,
        _facts(llm_reachable=True, book_endpoint_separate=True),
    ))
    assert checks["llm"].steps == ("clean",)
    assert checks["llm_book"].state == WARN and checks["llm_book"].steps == ("book",)


@pytest.mark.parametrize(
    "recipe_key, reachable, provider, key_set",
    list(itertools.product(
        ["transcript_only", "meeting_notes", "podcast_article", "youtube_video"],
        [True, False],
        ["lmstudio", "openai"],
        [True, False],
    )),
)
def test_matrix_every_llm_step_is_covered_by_exactly_one_check(
    recipe_key, reachable, provider, key_set,
):
    recipe = BUILTIN_RECIPES_BY_KEY[recipe_key]
    checks = recipe_checks(
        list(recipe.steps), LLM_STEPS,
        _facts(llm_provider=provider, cloud_key_set=key_set, llm_reachable=reachable),
    )
    llm_checks = [c for c in checks if c.key in ("llm", "llm_book", "llm_cloud")]
    covered = [step for c in llm_checks for step in c.steps]
    expected = [s for s in recipe.steps if s in LLM_STEPS]
    assert sorted(covered) == sorted(expected)
    for check in llm_checks:
        for step in check.steps:
            runs_in_cloud = effective_provider(step, provider) != "lmstudio"
            assert (check.key == "llm_cloud") == runs_in_cloud
        # A cloud key never makes a local step look ready.
        if check.key == "llm" and not reachable:
            assert check.state == MISSING
