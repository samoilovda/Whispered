"""Which transcription settings a launch uses: a recipe's own overrides win
over the header widgets. One rule for the single-file path and the queue,
so a queued file behaves like the same file started by hand."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def resolve_transcription(
    recipe_params: Mapping[str, Any] | None,
    recipe_steps: Sequence[str],
    widget_values: Mapping[str, Any],
) -> dict[str, Any]:
    """``{model, language, translate, performance_mode, diarization}``.

    A recipe without an override for a key falls back to *widget_values*
    (the header controls). A recipe with the ``diarize`` step needs
    speakers from the transcription pass itself, so it turns diarization on
    unless it says otherwise.
    """
    params = recipe_params or {}
    return {
        "model": params.get("model") or widget_values["model"],
        "language": params.get("language") or widget_values["language"],
        "translate": params["translate"] if "translate" in params else widget_values["translate"],
        "performance_mode": params.get("performance_mode") or widget_values["performance_mode"],
        "diarization": (
            params["diarization"] if "diarization" in params
            else "diarize" in recipe_steps or bool(widget_values["diarization"])
        ),
    }
