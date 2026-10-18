"""Choosing and applying a local AI profile (N05).

Qt-free. ``llm_params()`` calls :func:`params_for` so every LLM step —
single-step buttons, recipe runs and the queue — reads the same
endpoint / model / context / temperature. A queued file carries the profile
it was queued with (``QueueSnapshot.llm``), so changing the profile later
never changes a batch already waiting.
"""

from __future__ import annotations

from typing import Any, Optional

from domain.ai_profile import (
    DEFAULT_PROFILE, LmProfile, ProfileError, default_profile, profile_from_dict,
)


def user_profiles(cfg: Any) -> list[LmProfile]:
    """The profiles saved in settings; an invalid entry is skipped."""
    found = []
    for entry in getattr(cfg, "lm_profiles", None) or []:
        try:
            found.append(profile_from_dict(entry))
        except ProfileError:
            continue
    return found


def all_profiles(cfg: Any) -> list[LmProfile]:
    """"Default" (the plain settings) first, then the user's."""
    return [default_profile(cfg), *[p for p in user_profiles(cfg) if p.name != DEFAULT_PROFILE]]


def active_profile(cfg: Any) -> LmProfile:
    """The profile in use; the default when none is chosen or the chosen
    one was removed."""
    wanted = getattr(cfg, "lm_profile_active", "") or ""
    return next((p for p in all_profiles(cfg) if p.name == wanted), default_profile(cfg))


def params_for(profile: Optional[LmProfile]) -> dict:
    """The ``StepContext.params`` a non-default *profile* overrides."""
    if profile is None or profile.name == DEFAULT_PROFILE:
        return {}
    params: dict = {
        "lm_url": profile.endpoint, "book_lm_url": profile.endpoint,
        "insights_context_chars": profile.context_chars,
        "book_temperature": profile.temperature,
    }
    if profile.model:
        params["model"] = profile.model
        params["book_model"] = profile.model
    return params


def save_profile(cfg: Any, profile: LmProfile, replace: Optional[str] = None) -> None:
    """Add *profile* (or replace the one named *replace*) in the settings."""
    kept = [p for p in user_profiles(cfg) if p.name not in (profile.name, replace)]
    cfg.lm_profiles = [p.to_dict() for p in [*kept, profile]]
    if replace and getattr(cfg, "lm_profile_active", "") == replace:
        cfg.lm_profile_active = profile.name


def delete_profile(cfg: Any, name: str) -> None:
    cfg.lm_profiles = [p.to_dict() for p in user_profiles(cfg) if p.name != name]
    if getattr(cfg, "lm_profile_active", "") == name:
        cfg.lm_profile_active = ""
