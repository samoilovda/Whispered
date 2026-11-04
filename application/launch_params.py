"""Which transcription settings a launch uses: a recipe's own overrides win
over the header widgets. One rule for the single-file path and the queue,
so a queued file behaves like the same file started by hand."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence


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


def batch_models(items: Iterable[Any], default_model: str) -> list[str]:
    """The whisper models the queue's waiting files need, in a stable order:
    each file's own snapshot model, *default_model* for a file queued
    without one. Failed and cancelled files count: starting the queue
    retries them (BatchProcessor.start). Empty when nothing waits — no download is offered for a
    model no file uses (audit 2026-11-04 W14)."""
    needed: set[str] = set()
    for item in items:
        status = getattr(getattr(item, "status", None), "value", None)
        if status not in ("pending", "error", "cancelled"):
            continue
        snapshot = getattr(item, "transcription", None) or {}
        needed.add(snapshot.get("model_name") or default_model)
    return sorted(needed)
