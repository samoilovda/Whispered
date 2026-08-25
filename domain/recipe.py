"""Recipe — a named, editable set of job-engine steps plus the run
parameters they need (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B2).

Qt-free, and deliberately does not import application/steps.py: domain/
sits below application/ in this codebase's layering (see CLAUDE.md's
architecture map — "must never import ... core.live" is the letter of the
rule, staying below application/ is its spirit), so information about the
step registry has to flow the other way. ``Recipe.to_job_spec()`` takes a
``build_job_spec`` callable (``application.steps.build_job_spec``, at the
call site) instead of importing it, and ``KNOWN_STEP_NAMES`` below is a
plain, deliberately-duplicated set of the registry's step names rather
than an import of it — kept from drifting by
``tests/test_recipe.py::test_known_step_names_matches_the_registry``
(which, unlike this module, is free to import both sides) instead of by a
layering violation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from domain.job import JobSpec

# Mirrors application.steps.STEP_REGISTRY's keys as of B0's first step
# set. A user-authored recipe naming anything outside this set has that
# step silently dropped by from_dict() below — never crashes config load.
KNOWN_STEP_NAMES = frozenset({
    "transcribe", "diarize", "clean", "article",
    "insights", "youtube_package", "book", "cover",
})


@dataclass(frozen=True)
class Recipe:
    """A named sequence of steps plus the params their runners read
    (see application.steps.StepContext.params for what's recognized).

    ``builtin_key`` is empty for a user-authored recipe; one of the
    ``BUILTIN_RECIPES`` keys otherwise. It's kept distinct from ``name``
    so a user can rename their copy of a built-in without losing which
    built-in it started from.
    """

    name: str
    steps: "tuple[str, ...]"
    builtin_key: str = ""
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "steps": list(self.steps),
            "builtin_key": self.builtin_key,
            "params": dict(self.params),
        }

    @classmethod
    def from_dict(
        cls,
        data: dict,
        *,
        on_dropped_step: Optional[Callable[[str], None]] = None,
    ) -> "Recipe":
        """Build a Recipe from a stored dict, dropping any step name this
        build doesn't recognize instead of raising — a recipe saved by a
        newer version of the app (or hand-edited) must not break config
        load on an older one. *on_dropped_step* is called once per
        dropped name, e.g. so a caller with access to core.logger (this
        module deliberately has none) can log a warning.
        """
        raw_steps = data.get("steps", [])
        steps = []
        for step in raw_steps:
            if isinstance(step, str) and step in KNOWN_STEP_NAMES:
                steps.append(step)
            elif on_dropped_step is not None:
                on_dropped_step(str(step))
        return cls(
            name=str(data.get("name", "")),
            steps=tuple(steps),
            builtin_key=str(data.get("builtin_key", "")),
            params=dict(data.get("params") or {}),
        )

    def to_job_spec(
        self, build_job_spec: "Callable[[str, tuple[str, ...]], JobSpec]"
    ) -> JobSpec:
        """Build this recipe's JobSpec via *build_job_spec* — pass
        ``application.steps.build_job_spec`` at the call site."""
        return build_job_spec(self.name, self.steps)


def _builtin(key: str, steps: "tuple[str, ...]") -> Recipe:
    return Recipe(name=key, steps=steps, builtin_key=key)


# Built-in recipes (docs/UI_REDESIGN_PLAN_2026-09.ru.md, B2). ``name``
# doubles as a display-name lookup key (i18n keys are "recipe_<name>",
# wired up wherever these are actually shown — see B4/B6) and is what
# Config.last_recipe stores.
TRANSCRIPT_ONLY = _builtin("transcript_only", ("transcribe",))
YOUTUBE_VIDEO = _builtin(
    "youtube_video", ("transcribe", "clean", "youtube_package", "cover")
)
PODCAST_ARTICLE = _builtin("podcast_article", ("transcribe", "clean", "article"))
MEETING_NOTES = _builtin("meeting_notes", ("transcribe", "diarize", "insights"))
BOOK = _builtin("book", ("transcribe", "clean", "book"))

BUILTIN_RECIPES: "tuple[Recipe, ...]" = (
    TRANSCRIPT_ONLY, YOUTUBE_VIDEO, PODCAST_ARTICLE, MEETING_NOTES, BOOK,
)
BUILTIN_RECIPES_BY_KEY = {recipe.builtin_key: recipe for recipe in BUILTIN_RECIPES}
