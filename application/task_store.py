"""Where a record's task cards live: the insights overlay (N04)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from application.user_edits import load_overlay, overlay_path, save_overlay
from domain import tasks as t

OVERLAY_KEY = "tasks"


def overlay_file(artifact_dir: Path) -> Path:
    """``<dir>/insights.user.json`` — beside the model's insights.json."""
    return overlay_path(artifact_dir / "insights.json")


def load_cards(artifact_dir: Path) -> Optional[list[t.TaskCard]]:
    """The stored cards; ``[]`` when none; ``None`` for a newer schema."""
    data = load_overlay(overlay_file(artifact_dir)).get(OVERLAY_KEY)
    return t.tasks_from_dict(data) if data is not None else []


def save_cards(artifact_dir: Path, cards: list[t.TaskCard]) -> None:
    """Write the touched cards; other overlay keys stay as they are."""
    path = overlay_file(artifact_dir)
    overlay = load_overlay(path)
    stored = t.tasks_to_dict(cards)
    if stored["cards"]:
        overlay[OVERLAY_KEY] = stored
    else:
        overlay.pop(OVERLAY_KEY, None)
    save_overlay(path, overlay)
