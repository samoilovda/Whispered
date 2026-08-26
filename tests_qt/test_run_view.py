"""Real-Qt tests for ui/run_view.py (see
docs/UI_REDESIGN_PLAN_2026-09.ru.md, B4).
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QLabel

from application.job_engine import JobRun
from core.i18n import load_locale, tr
from domain.job import JobSpec, StepOutcome, StepSpec, StepStatus
from ui.run_view import RunView

STEP_NAMES = (
    "transcribe", "diarize", "clean", "article",
    "insights", "youtube_package", "book", "cover",
)
LABELS = {name: name.replace("_", " ").title() for name in STEP_NAMES}


@pytest.fixture(autouse=True)
def _pin_english_locale():
    # core.i18n's current language is process-global state other test
    # modules mutate (see test_ui_audit_regressions.py) — pin it so this
    # module's tr()-based assertions don't depend on test run order.
    load_locale("en")
    yield


def _spec(names=STEP_NAMES) -> JobSpec:
    return JobSpec(name="test", steps=tuple(StepSpec(n) for n in names))


def _run(outcomes: dict, names=STEP_NAMES) -> JobRun:
    run = JobRun(spec=_spec(names))
    run.outcomes.update(outcomes)
    return run


# ------------------------------------------------------------------ bind_run reflects a fixture

def test_bind_run_shows_waiting_for_every_step_with_no_outcome(process_events):
    view = RunView(STEP_NAMES, LABELS)
    run = _run({})
    view.bind_run(run)
    process_events()

    for row in view.rows().values():
        assert tr("run_status_waiting") in row._status_badge.text()


def test_bind_run_hides_rows_the_recipe_does_not_run(process_events):
    """MainWindow builds one row per registered step, but a recipe runs a
    subset — the rest used to sit at "waiting" forever after the run
    finished, reading as a run that still had work left to do."""
    view = RunView(STEP_NAMES, LABELS)
    run = _run(
        {"transcribe": StepOutcome("transcribe", StepStatus.SUCCEEDED)},
        names=("transcribe",),
    )
    view.bind_run(run)
    view.show()
    process_events()

    rows = view.rows()
    assert rows["transcribe"].isVisible()
    for name in STEP_NAMES[1:]:
        assert not rows[name].isVisible(), name
    view.close()


def test_bind_run_reshows_a_row_a_later_recipe_does_run(process_events):
    """Rows are hidden per bound run, not permanently — the same widget
    is reused for the next recipe."""
    view = RunView(STEP_NAMES, LABELS)
    view.bind_run(_run({}, names=("transcribe",)))
    view.show()
    process_events()
    assert not view.rows()["clean"].isVisible()

    view.bind_run(_run({}, names=("transcribe", "clean")))
    process_events()
    assert view.rows()["clean"].isVisible()
    view.close()


def test_bind_run_reflects_mixed_outcomes(process_events):
    view = RunView(STEP_NAMES, LABELS)
    run = _run({
        "transcribe": StepOutcome("transcribe", StepStatus.SUCCEEDED),
        "clean": StepOutcome("clean", StepStatus.SKIPPED),
        "article": StepOutcome("article", StepStatus.FAILED, error="LM Studio timed out"),
        "insights": StepOutcome("insights", StepStatus.CANCELLED),
    })
    view.bind_run(run)
    view.show()
    process_events()

    rows = view.rows()
    assert tr("run_status_waiting") in rows["diarize"]._status_badge.text()
    assert tr("status_error", error="LM Studio timed out") in rows["article"]._status_badge.text()
    assert rows["article"]._retry_button.isVisible()
    assert not rows["clean"]._retry_button.isVisible()
    assert rows["insights"]._retry_button.isVisible()
    assert not rows["transcribe"]._retry_button.isVisible()


def test_only_steps_with_a_result_and_a_viewer_show_a_chevron(process_events):
    viewer = QLabel("clean output")
    view = RunView(STEP_NAMES, LABELS, viewers={"clean": viewer})
    run = _run({
        "transcribe": StepOutcome("transcribe", StepStatus.SUCCEEDED),
        "clean": StepOutcome("clean", StepStatus.SUCCEEDED),
    })
    view.bind_run(run)
    view.show()
    process_events()

    rows = view.rows()
    # transcribe succeeded but has no viewer entry -> no chevron.
    assert not rows["transcribe"]._chevron.isVisible()
    # clean succeeded and has a viewer -> chevron shown.
    assert rows["clean"]._chevron.isVisible()
    # untouched steps never show a chevron regardless of viewer wiring.
    assert not rows["book"]._chevron.isVisible()


def test_expanding_a_chevron_shows_its_viewer(process_events):
    viewer = QLabel("article output")
    view = RunView(STEP_NAMES, LABELS, viewers={"article": viewer})
    run = _run({"article": StepOutcome("article", StepStatus.SUCCEEDED)})
    view.bind_run(run)
    view.show()
    process_events()

    row = view.rows()["article"]
    assert not viewer.isVisible() or not row._body.isVisible()
    row._chevron.setChecked(True)
    process_events()
    assert row._body.isVisible()
    row._chevron.setChecked(False)
    process_events()
    assert not row._body.isVisible()


# ------------------------------------------------------------------ retry

def test_retry_click_resets_only_the_clicked_step(process_events):
    view = RunView(STEP_NAMES, LABELS)
    run = _run({
        "article": StepOutcome("article", StepStatus.FAILED, error="boom"),
        "book": StepOutcome("book", StepStatus.FAILED, error="also boom"),
    })
    view.bind_run(run)
    process_events()

    view.rows()["article"]._retry_button.click()
    process_events()

    assert "article" not in run.outcomes
    assert "book" in run.outcomes
    assert run.outcomes["book"].status is StepStatus.FAILED


def test_retry_click_resyncs_cascaded_cancelled_dependents(process_events):
    """reset_step() on domain/job.py's JobRun cascades to a dependent that
    was only CANCELLED because this step hadn't succeeded — the row for
    that dependent must go back to "waiting" too."""
    names = ("clean", "article")
    view = RunView(names, {"clean": "Clean", "article": "Article"})
    spec = JobSpec(name="test", steps=(
        StepSpec("clean"), StepSpec("article", depends_on=("clean",)),
    ))
    run = JobRun(spec=spec)
    run.outcomes["clean"] = StepOutcome("clean", StepStatus.FAILED, error="x")
    run.outcomes["article"] = StepOutcome("article", StepStatus.CANCELLED)
    view.bind_run(run)
    process_events()

    view.rows()["clean"]._retry_button.click()
    process_events()

    assert "clean" not in run.outcomes
    assert "article" not in run.outcomes
    assert tr("run_status_waiting") in view.rows()["article"]._status_badge.text()


def test_retry_click_emits_retry_requested_with_the_step_name(process_events):
    view = RunView(STEP_NAMES, LABELS)
    run = _run({"cover": StepOutcome("cover", StepStatus.FAILED, error="x")})
    view.bind_run(run)
    process_events()

    seen = []
    view.retry_requested.connect(seen.append)
    view.rows()["cover"]._retry_button.click()
    process_events()

    assert seen == ["cover"]


def test_retry_without_a_bound_run_still_emits_the_signal(process_events):
    view = RunView(("clean",), {"clean": "Clean"})
    row = view.rows()["clean"]
    row.apply_outcome(StepOutcome("clean", StepStatus.FAILED, error="x"))
    process_events()

    seen = []
    view.retry_requested.connect(seen.append)
    row._retry_button.click()
    process_events()

    assert seen == ["clean"]


# ------------------------------------------------------------------ JobRunner signal targets

def test_on_step_started_shows_running_state(process_events):
    view = RunView(("clean",), {"clean": "Clean"})
    view.show()
    view.on_step_started("clean")
    process_events()
    row = view.rows()["clean"]
    assert tr("run_status_running") in row._status_badge.text()
    assert row._cancel_button.isVisible()


def test_on_step_progress_updates_the_progress_bar_and_message(process_events):
    view = RunView(("youtube_package",), {"youtube_package": "YouTube"})
    view.on_step_started("youtube_package")
    view.on_step_progress("youtube_package", 40, "chapters 2/5")
    process_events()
    row = view.rows()["youtube_package"]
    assert row._progress.value() == 40
    assert "chapters 2/5" in row._status_badge.text()


def test_on_step_finished_applies_the_outcome_to_the_right_row(process_events):
    view = RunView(("clean", "article"), {"clean": "Clean", "article": "Article"})
    view.on_step_started("clean")
    view.on_step_finished("clean", StepOutcome("clean", StepStatus.SUCCEEDED))
    process_events()

    rows = view.rows()
    assert not rows["clean"]._cancel_button.isVisible()
    assert tr("run_status_waiting") in rows["article"]._status_badge.text()


def test_on_job_finished_resyncs_every_row(process_events):
    view = RunView(("clean", "article"), {"clean": "Clean", "article": "Article"})
    run = _run(
        {
            "clean": StepOutcome("clean", StepStatus.SUCCEEDED),
            "article": StepOutcome("article", StepStatus.SUCCEEDED),
        },
        names=("clean", "article"),
    )
    view.on_job_finished(run)
    process_events()

    rows = view.rows()
    assert not rows["clean"]._retry_button.isVisible()
    assert not rows["article"]._retry_button.isVisible()


# ------------------------------------------------------------------ layout budget

def test_eight_collapsed_steps_fit_900x550_without_scrolling(qt_application, process_events):
    view = RunView(STEP_NAMES, LABELS)
    run = _run({
        "transcribe": StepOutcome("transcribe", StepStatus.SUCCEEDED),
        "diarize": StepOutcome("diarize", StepStatus.SKIPPED),
        "clean": StepOutcome("clean", StepStatus.SUCCEEDED),
        "article": StepOutcome("article", StepStatus.SUCCEEDED),
        "insights": StepOutcome("insights", StepStatus.SUCCEEDED),
        "youtube_package": StepOutcome("youtube_package", StepStatus.FAILED, error="x"),
        "book": StepOutcome("book", StepStatus.CANCELLED),
    })
    view.bind_run(run)
    view.resize(900, 550)
    view.show()
    process_events()
    process_events()

    bar = view._scroll.verticalScrollBar()
    assert bar.maximum() == 0, f"content needs scrolling: maximum={bar.maximum()}"
    view.close()
