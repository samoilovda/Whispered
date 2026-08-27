"""Real-Qt tests for ui/command_palette.py's B8 additions: recipes and a
bound RunView's retriable steps alongside the existing actions/records
(see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B8).
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from application.job_engine import JobRun
from core.i18n import load_locale, tr
from domain.job import JobSpec, StepOutcome, StepSpec, StepStatus
from ui.command_palette import CommandPalette
from ui.run_view import RunView


@pytest.fixture(autouse=True)
def _pin_english_locale():
    # core.i18n's current language is process-global state other test
    # modules mutate — pin it so this module's tr()-based assertions
    # don't depend on test run order (see test_run_view.py).
    load_locale("en")
    yield


def _payloads(palette: CommandPalette) -> list:
    return [
        palette.results.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(palette.results.count())
    ]


def test_palette_lists_every_builtin_recipe(process_events):
    palette = CommandPalette()
    palette._refresh("")
    process_events()

    kinds = {value for kind, value in _payloads(palette) if kind == "recipe"}
    assert kinds == {
        "transcript_only", "youtube_video", "podcast_article",
        "meeting_notes", "book",
    }


def test_recipe_query_finds_it_by_label(process_events):
    palette = CommandPalette()
    palette._refresh("youtube")
    process_events()

    assert ("recipe", "youtube_video") in _payloads(palette)


def test_activating_a_recipe_item_emits_recipe_requested(process_events):
    palette = CommandPalette()
    palette._refresh("")
    process_events()

    seen = []
    palette.recipe_requested.connect(seen.append)
    item = next(
        palette.results.item(i) for i in range(palette.results.count())
        if palette.results.item(i).data(Qt.ItemDataRole.UserRole) == ("recipe", "book")
    )
    palette._activate(item)

    assert seen == ["book"]


def test_no_retriable_steps_without_a_bound_run_view(process_events):
    palette = CommandPalette()
    palette._refresh("")
    process_events()

    assert not any(kind == "retry_step" for kind, _ in _payloads(palette))


def test_bound_run_view_surfaces_its_failed_step(process_events):
    run_view = RunView(("clean", "article"), {"clean": "Clean", "article": "Article"})
    spec = JobSpec(name="test", steps=(StepSpec("clean"), StepSpec("article")))
    run = JobRun(spec=spec)
    run.outcomes["article"] = StepOutcome("article", StepStatus.FAILED, error="boom")
    run_view.bind_run(run)

    palette = CommandPalette()
    palette.bind_run_view(run_view)
    palette._refresh("")
    process_events()

    assert ("retry_step", "article") in _payloads(palette)
    # "clean" never started (no outcome at all) -> not retriable.
    assert ("retry_step", "clean") not in _payloads(palette)


def test_step_query_finds_it_by_label(process_events):
    run_view = RunView(("cover",), {"cover": "Cover"})
    spec = JobSpec(name="test", steps=(StepSpec("cover"),))
    run = JobRun(spec=spec)
    run.outcomes["cover"] = StepOutcome("cover", StepStatus.FAILED, error="boom")
    run_view.bind_run(run)

    palette = CommandPalette()
    palette.bind_run_view(run_view)
    palette._refresh("cover")
    process_events()

    assert ("retry_step", "cover") in _payloads(palette)


def test_one_query_can_find_both_a_recipe_and_a_step(process_events):
    """B8's acceptance criterion: the palette finds a recipe and a step
    in one query."""
    run_view = RunView(("book",), {"book": tr("recipe_book")})
    spec = JobSpec(name="test", steps=(StepSpec("book"),))
    run = JobRun(spec=spec)
    run.outcomes["book"] = StepOutcome("book", StepStatus.FAILED, error="boom")
    run_view.bind_run(run)

    palette = CommandPalette()
    palette.bind_run_view(run_view)
    palette._refresh(tr("recipe_book"))
    process_events()

    kinds = {kind for kind, _ in _payloads(palette)}
    assert kinds == {"recipe", "retry_step"}


# ------------------------------------------------------------------ B12: menu-derived actions

def test_every_palette_marked_menu_action_is_findable(process_events):
    """B12 acceptance criterion: every QAction MainWindow._init_menu_bar
    marks in_palette=True must show up in the palette with the same
    caption — bind_actions() is handed exactly that list, so this is
    really asserting bind_actions()/_refresh() don't drop or rename any
    of them."""
    from ui.main_window import MainWindow

    window = MainWindow()
    palette = window.command_palette
    palette._refresh("")
    process_events()

    labels = {value.text() for kind, value in _payloads(palette) if kind == "action"}
    expected = {action.text() for action in window._palette_menu_actions}
    assert labels == expected
    assert expected, "menu bar produced no palette-eligible actions at all"

    window.close()
    process_events()


def test_activating_an_action_row_triggers_the_same_qaction_the_menu_uses(
    process_events,
):
    from PyQt6.QtCore import Qt
    from ui.main_window import MainWindow

    window = MainWindow()
    palette = window.command_palette
    palette._refresh("")
    process_events()

    target = window._palette_menu_actions[0]
    calls = []
    target.triggered.connect(lambda: calls.append(True))

    item = next(
        palette.results.item(i) for i in range(palette.results.count())
        if palette.results.item(i).data(Qt.ItemDataRole.UserRole) == ("action", target)
    )
    palette._activate(item)

    assert calls == [True]

    window.close()
    process_events()


def test_a_disabled_action_is_shown_but_not_triggerable(process_events):
    """B12 item 4: an inapplicable action stays visible (so the user
    knows the function exists) but inactive — activating it must not
    fire triggered()."""
    from PyQt6.QtCore import Qt
    from ui.main_window import MainWindow

    window = MainWindow()
    palette = window.command_palette
    target = window._palette_menu_actions[0]
    target.setEnabled(False)
    palette._refresh("")
    process_events()

    item = next(
        palette.results.item(i) for i in range(palette.results.count())
        if palette.results.item(i).data(Qt.ItemDataRole.UserRole) == ("action", target)
    )
    assert not (item.flags() & Qt.ItemFlag.ItemIsEnabled)

    calls = []
    target.triggered.connect(lambda: calls.append(True))
    palette._activate(item)

    assert calls == []

    window.close()
    process_events()


def test_activating_a_retry_step_item_emits_retry_step_requested(process_events):
    run_view = RunView(("cover",), {"cover": "Cover"})
    spec = JobSpec(name="test", steps=(StepSpec("cover"),))
    run = JobRun(spec=spec)
    run.outcomes["cover"] = StepOutcome("cover", StepStatus.FAILED, error="boom")
    run_view.bind_run(run)

    palette = CommandPalette()
    palette.bind_run_view(run_view)
    palette._refresh("")
    process_events()

    seen = []
    palette.retry_step_requested.connect(seen.append)
    item = next(
        palette.results.item(i) for i in range(palette.results.count())
        if palette.results.item(i).data(Qt.ItemDataRole.UserRole) == ("retry_step", "cover")
    )
    palette._activate(item)

    assert seen == ["cover"]
