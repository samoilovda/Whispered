"""build_step_context / llm_params / artifact_types_for_steps."""

from types import SimpleNamespace

from application.steps import (
    STEP_ARTIFACT_TYPES,
    artifact_types_for_steps,
    build_step_context,
    llm_params,
)
from domain.transcription import TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(segments=[], language="ru", duration=1.0)


def test_artifact_types_ignore_untracked_steps():
    assert artifact_types_for_steps(["clean", "youtube_package", "book", "cover"]) == {
        "youtube",
        "book",
    }
    assert set(STEP_ARTIFACT_TYPES.values()) == {"article", "insights", "youtube", "book"}


def test_llm_params_lmstudio_provider_is_none():
    cfg = SimpleNamespace(lm_studio_url="http://x/v1", yt_language="en")
    params = llm_params(
        cfg, _result(), provider=SimpleNamespace(kind="lmstudio"), insights_cache="c", k=1
    )
    assert params["lm_url"] == "http://x/v1"
    assert params["provider"] is None
    assert params["yt_language"] == "en"
    assert params["insights_cache"] == "c"
    assert params["k"] == 1
    assert "language" in params


def test_llm_params_minimal():
    cfg = SimpleNamespace(lm_studio_url="u", yt_language="")
    assert llm_params(cfg) == {"lm_url": "u"}


def test_build_step_context_unsaved_and_out_dir(tmp_path):
    ctx = build_step_context("a.wav", _result(), None, out_dir=tmp_path)
    assert ctx.record_id == "unsaved"
    assert ctx.artifact_dir == tmp_path
    assert ctx.params == {}
