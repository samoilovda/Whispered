"""Simple speech-recognition profiles over the exact settings (UI/UX audit
2026-10-18, UX-16 / T16).

A profile is a name for a (model, performance mode) pair. Choosing one
sets those two settings; the exact model and mode stay editable in the
recipe editor and Settings ("advanced"), and a configuration that matches
no profile is shown as "custom" — a profile never hides what is selected.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AsrProfile:
    key: str
    model: str           # utils.WHISPER_MODELS key
    performance_mode: str  # utils.PERFORMANCE_MODES key


PROFILES: tuple[AsrProfile, ...] = (
    AsrProfile("fast", "large-v3-turbo-q5_0", "performance"),
    AsrProfile("balanced", "large-v3-turbo-q5_0", "balanced"),
    AsrProfile("accurate", "large-v3-turbo-q8_0", "balanced"),
)
PROFILES_BY_KEY = {p.key: p for p in PROFILES}
CUSTOM = "custom"


def profile_for(model: str, performance_mode: str) -> str:
    """The profile key matching the exact settings, else ``"custom"``."""
    for profile in PROFILES:
        if profile.model == model and profile.performance_mode == performance_mode:
            return profile.key
    return CUSTOM
