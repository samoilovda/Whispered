"""application.readiness: what the start screen says about a recipe."""

from application.readiness import (
    CHECKING, FIX_FFMPEG, FIX_SETTINGS_AI, MISSING, OK, WARN, ReadinessFacts, recipe_checks,
)

LLM = ("clean", "article", "insights", "youtube_package", "book")


def _facts(**kw):
    base = dict(model_label="Turbo", model_downloaded=True, ffmpeg_found=True)
    base.update(kw)
    return ReadinessFacts(**base)


def _by_key(checks):
    return {c.key: c for c in checks}


def test_transcript_only_needs_no_llm():
    checks = _by_key(recipe_checks(("transcribe",), LLM, _facts()))
    assert set(checks) == {"model", "ffmpeg"}
    assert all(c.state == OK for c in checks.values())


def test_llm_state_follows_the_probe():
    steps = ("transcribe", "insights")
    assert _by_key(recipe_checks(steps, LLM, _facts()))["llm"].state == CHECKING
    assert _by_key(recipe_checks(steps, LLM, _facts(llm_reachable=True)))["llm"].state == OK
    missing = _by_key(recipe_checks(steps, LLM, _facts(llm_reachable=False)))["llm"]
    assert missing.state == MISSING and missing.fix == FIX_SETTINGS_AI


def test_cloud_provider_needs_a_key_not_lm_studio():
    steps = ("transcribe", "youtube_package")
    check = _by_key(recipe_checks(steps, LLM, _facts(llm_provider="openai", llm_reachable=False)))["llm"]
    assert check.state == MISSING and check.params == {"provider": "OpenAI"}
    check = _by_key(recipe_checks(steps, LLM, _facts(llm_provider="openai", cloud_key_set=True)))["llm"]
    assert check.state == OK


def test_missing_pieces_name_their_fix():
    checks = _by_key(recipe_checks(
        ("transcribe", "diarize"), LLM,
        _facts(model_downloaded=False, ffmpeg_found=False, diarize_ready=False),
    ))
    assert checks["model"].state == WARN
    assert checks["ffmpeg"].state == MISSING and checks["ffmpeg"].fix == FIX_FFMPEG
    assert checks["diarize"].state == WARN and checks["diarize"].fix
