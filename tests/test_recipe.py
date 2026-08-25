"""Unit tests for domain/recipe.py (see
docs/UI_REDESIGN_PLAN_2026-09.ru.md, B2).
"""

from __future__ import annotations

import pytest

from domain.recipe import (
    BUILTIN_RECIPES,
    BUILTIN_RECIPES_BY_KEY,
    BOOK,
    KNOWN_STEP_NAMES,
    MEETING_NOTES,
    PODCAST_ARTICLE,
    Recipe,
    TRANSCRIPT_ONLY,
    YOUTUBE_VIDEO,
)


def test_known_step_names_matches_the_registry():
    """domain/recipe.py deliberately duplicates application/steps.py's
    step name set instead of importing it (see the module docstring) —
    this is the guard against the two drifting apart. Only this test is
    allowed to import both sides of that boundary."""
    from application.steps import STEP_REGISTRY

    assert KNOWN_STEP_NAMES == set(STEP_REGISTRY)


@pytest.mark.parametrize(
    ("recipe", "expected_steps"),
    [
        (TRANSCRIPT_ONLY, ("transcribe",)),
        (YOUTUBE_VIDEO, ("transcribe", "clean", "youtube_package", "cover")),
        (PODCAST_ARTICLE, ("transcribe", "clean", "article")),
        (MEETING_NOTES, ("transcribe", "diarize", "insights")),
        (BOOK, ("transcribe", "clean", "book")),
    ],
)
def test_builtin_recipes_match_the_plan(recipe, expected_steps):
    assert recipe.steps == expected_steps
    assert recipe.builtin_key == recipe.name


def test_builtin_recipes_registered_by_key():
    assert set(BUILTIN_RECIPES_BY_KEY) == {r.builtin_key for r in BUILTIN_RECIPES}
    assert BUILTIN_RECIPES_BY_KEY["youtube_video"] is YOUTUBE_VIDEO


@pytest.mark.parametrize("recipe", BUILTIN_RECIPES)
def test_every_builtin_recipe_yields_a_valid_job_spec(recipe):
    from application.steps import build_job_spec

    spec = recipe.to_job_spec(build_job_spec)
    order = spec.topological_order()
    assert set(order) == set(recipe.steps)


def test_every_builtin_step_name_is_known():
    for recipe in BUILTIN_RECIPES:
        assert set(recipe.steps) <= KNOWN_STEP_NAMES


# ------------------------------------------------------------------ to_dict/from_dict

def test_round_trip_through_dict():
    original = Recipe(
        name="my recipe", steps=("transcribe", "clean", "article"),
        builtin_key="", params={"model": "turbo"},
    )
    restored = Recipe.from_dict(original.to_dict())
    assert restored == original


def test_from_dict_drops_unknown_steps_without_raising():
    data = {"name": "weird", "steps": ["transcribe", "levitate", "clean"]}
    recipe = Recipe.from_dict(data)
    assert recipe.steps == ("transcribe", "clean")


def test_from_dict_reports_dropped_steps_via_callback():
    dropped = []
    data = {"name": "weird", "steps": ["transcribe", "levitate", "teleport"]}
    Recipe.from_dict(data, on_dropped_step=dropped.append)
    assert dropped == ["levitate", "teleport"]


def test_from_dict_tolerates_missing_and_malformed_fields():
    recipe = Recipe.from_dict({})
    assert recipe.name == ""
    assert recipe.steps == ()
    assert recipe.params == {}

    # A non-string entry in "steps" must not raise either.
    recipe = Recipe.from_dict({"steps": ["transcribe", 42, None]})
    assert recipe.steps == ("transcribe",)


def test_to_job_spec_passes_name_and_steps_to_the_builder():
    recipe = Recipe(name="custom", steps=("transcribe", "clean"))
    calls = []

    def fake_build_job_spec(name, steps):
        calls.append((name, steps))
        return "sentinel-spec"

    result = recipe.to_job_spec(fake_build_job_spec)
    assert result == "sentinel-spec"
    assert calls == [("custom", ("transcribe", "clean"))]
