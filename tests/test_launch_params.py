"""application/launch_params.py — the recipe-over-widgets rule shared by the
single-file path and the queue."""

from __future__ import annotations

from application.launch_params import resolve_transcription

WIDGETS = {"model": "large-v3-turbo", "language": "auto", "translate": False,
           "performance_mode": "balanced", "diarization": False}


def test_recipe_overrides_beat_the_header_widgets():
    out = resolve_transcription({"model": "tiny", "language": "en", "translate": True}, ("transcribe",), WIDGETS)
    assert (out["model"], out["language"], out["translate"]) == ("tiny", "en", True)
    assert out["performance_mode"] == "balanced" and out["diarization"] is False


def test_without_overrides_the_widgets_decide():
    assert resolve_transcription({}, ("transcribe",), WIDGETS) == {
        "model": "large-v3-turbo", "language": "auto", "translate": False,
        "performance_mode": "balanced", "diarization": False}
    assert resolve_transcription(None, ("transcribe",), WIDGETS)["model"] == "large-v3-turbo"


def test_the_diarize_step_turns_diarization_on_unless_the_recipe_says_otherwise():
    assert resolve_transcription({}, ("transcribe", "diarize"), WIDGETS)["diarization"] is True
    assert resolve_transcription({"diarization": False}, ("diarize",), WIDGETS)["diarization"] is False
    assert resolve_transcription({"translate": False}, (), {**WIDGETS, "translate": True})["translate"] is False
