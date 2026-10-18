"""Results made before a record's source was replaced (UX-09 / T09)."""

from __future__ import annotations

from typing import Any


def made_before(context: Any, step: str, changed_at: str) -> bool:
    """Whether step *step*'s saved result for *context* predates
    *changed_at* (ISO time the record's source was replaced). False when
    there is no saved result, no timestamp, or nothing was replaced."""
    if not changed_at:
        return False
    from application.steps import STEP_REGISTRY
    from infrastructure.persistence import artifact_store

    definition = STEP_REGISTRY.get(step)
    artifact = definition.make_artifact(context) if definition is not None else None
    if artifact is None:
        return False
    saved = artifact_store.load(artifact.path)
    if saved is None or not saved.created_at:
        return False
    return _instant(saved.created_at) < _instant(changed_at)


def _instant(iso: str) -> float:
    from datetime import datetime, timezone

    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()
