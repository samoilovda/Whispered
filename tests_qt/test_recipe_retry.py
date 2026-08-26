"""Real-Qt regressions for retrying a step of a recipe run that was
cancelled, and for the cover step reading the Cover workspace's own
settings (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B6/B8).
"""

from __future__ import annotations

import threading

import pytest

from application.job_engine import JobRun
from application.steps import build_job_spec
from core.job_runner import JobRunner
from domain.job import StepOutcome, StepStatus
from transcriber import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello world")], language="en", duration=1.0,
    )


# ---------------------------------------------------------------- the engine-level trap

def test_a_cancelled_job_run_never_runs_another_step(process_events):
    """Why MainWindow can't just reuse a cancelled JobRun on retry:
    JobRun.cancel() latches an Event nothing clears, and JobEngine marks
    every step CANCELLED before running it."""
    spec = build_job_spec("t", ("transcribe", "clean"))
    run = JobRun(spec=spec)
    run.outcomes["transcribe"] = StepOutcome("transcribe", StepStatus.SUCCEEDED)

    JobRunner(spec, run_state=run).cancel()      # what registry.retire() does
    assert run.is_cancelled()

    calls = []
    runner = JobRunner(spec, run_state=run)
    runner.set_runners({"transcribe": lambda: None, "clean": lambda: calls.append("ran")})
    runner.start()
    assert runner.wait(3000)
    process_events()

    assert calls == []
    assert run.outcomes["clean"].status is StepStatus.CANCELLED


# ---------------------------------------------------------------- MainWindow retry path

@pytest.fixture
def window(monkeypatch, tmp_path):
    import config
    import core.history as history

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config(last_recipe="podcast_article"))
    store = history.HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr(history, "_store", store)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    from ui.main_window import MainWindow

    win = MainWindow()
    yield win
    win.close()


def test_retry_after_cancelling_a_run_actually_reruns_the_step(window, monkeypatch, process_events):
    """Regression: cancelling a recipe run used to leave its JobRun
    permanently cancelled, so every later retry resolved CANCELLED
    without running anything — retry was dead for the rest of the
    session."""
    started = threading.Event()
    release = threading.Event()
    ran = []

    class _BlockingProcessor:
        def __init__(self, *_a, **_k):
            self.lm_client = type("C", (), {"is_cancelled": None})()

        def process(self, text, use_ai=True, on_progress=None):
            ran.append(text)
            started.set()
            release.wait(5)
            raise RuntimeError("cancelled")

    monkeypatch.setattr("text_processor.TextProcessor", _BlockingProcessor)

    window._run_recipe(_result())
    assert started.wait(3)

    window._cancel_recipe_job()
    release.set()
    process_events()
    assert window._recipe_run.is_cancelled()

    # Second attempt: a runner that succeeds immediately.
    class _FastProcessor(_BlockingProcessor):
        def process(self, text, use_ai=True, on_progress=None):
            from text_processor import CleanedText, CoherentText, ProcessingResult

            ran.append(text)
            return ProcessingResult(
                original=text,
                cleaned=CleanedText(
                    original=text, cleaned=text, removed_fillers=0,
                    sentences_fixed=0, paragraphs_created=1,
                ),
                coherent=CoherentText(text=text, paragraphs=[text]),
                processing_time=0.0,
            )

    monkeypatch.setattr("text_processor.TextProcessor", _FastProcessor)

    window.run_view.retry_step("clean")
    process_events()
    runner = window._recipe_job
    assert runner is not None
    assert runner.wait(5000)
    process_events()

    assert len(ran) == 2, "the retried step never executed"
    assert window._recipe_run.outcomes["clean"].status is StepStatus.SUCCEEDED
    assert not window._recipe_run.is_cancelled()


def test_retry_keeps_the_already_succeeded_steps(window, monkeypatch, process_events):
    """The fresh JobRun a cancelled retry builds must carry the finished
    outcomes forward, or the run screen would forget them."""
    window._run_recipe(_result())
    assert window._recipe_job.wait(3000)
    process_events()

    outcomes_before = dict(window._recipe_run.outcomes)
    assert outcomes_before["transcribe"].status is StepStatus.SUCCEEDED

    window._recipe_run.cancel()
    window.run_view.retry_step("clean")
    process_events()
    if window._recipe_job is not None:
        window._recipe_job.wait(3000)
    process_events()

    assert window._recipe_run.outcomes["transcribe"].status is StepStatus.SUCCEEDED
    assert window.run_view._run is window._recipe_run


# ---------------------------------------------------------------- finished status

def test_finished_run_clears_the_running_status_line(window, process_events):
    """Regression: _reset_ui() only hides the progress/cancel widgets, so
    the one persistent status line kept reading "Running the recipe…"
    after the run had ended — nothing else writes to it until the next
    operation starts."""
    from core.i18n import tr

    window._run_recipe(_result())
    assert window._recipe_job.wait(10000)
    process_events()

    assert window._recipe_job is None
    text = window.status_label.text()
    assert tr("status_chain_running") not in text
    assert text in (
        tr("status_chain_failed"),
        *(tr("status_chain_done", count=n) for n in range(0, 9)),
    )


def test_finished_run_offers_the_way_to_the_record(window, process_events):
    """The run screen names its recipe while running and grows a route to
    the record once the run ends."""
    # isVisibleTo(), not isVisible(): this fixture never show()s the
    # window, so every descendant reports isVisible() False regardless.
    window._run_recipe(_result())
    assert window.run_view._heading.isVisibleTo(window.run_view)
    assert window.run_view._heading.text()
    assert not window.run_view._open_button.isVisibleTo(window.run_view)

    assert window._recipe_job.wait(10000)
    process_events()

    assert window.run_view._open_button.isVisibleTo(window.run_view)
    window.run_view._open_button.click()
    process_events()
    assert window._stack.currentIndex() == window._record_index


def test_cancelling_from_a_step_row_reports_it_like_the_status_bar_does(
    window, monkeypatch, process_events,
):
    """Regression: RunView's per-step Cancel goes straight to
    _cancel_recipe_job, which retires the runner — so job_finished never
    fires and nothing reset the screen. The status bar kept reading
    "Running the recipe…", its Cancel button stayed up, and the run screen
    offered no way out."""
    import threading

    from core.i18n import tr

    started = threading.Event()
    release = threading.Event()

    class _BlockingProcessor:
        def __init__(self, *_a, **_k):
            self.lm_client = type("C", (), {"is_cancelled": None})()

        def process(self, text, use_ai=True, on_progress=None):
            started.set()
            release.wait(5)
            raise RuntimeError("cancelled")

    monkeypatch.setattr("text_processor.TextProcessor", _BlockingProcessor)

    window._run_recipe(_result())
    assert started.wait(3)

    window.run_view.cancel_requested.emit()
    release.set()
    process_events()

    assert window.status_label.text() == tr("status_chain_cancelled")
    assert not window.cancel_btn.isVisibleTo(window.status_bar)
    assert window.run_view._open_button.isVisibleTo(window.run_view)


# ---------------------------------------------------------------- cover params

def test_recipe_run_renders_the_cover_the_workspace_is_set_to(window, monkeypatch, process_events):
    """Regression: the "YouTube video" recipe includes the cover step,
    but _run_recipe passed no cover_* params at all, so it silently
    rendered with _cover_runner's own fallbacks (layout "solo") instead
    of the user's Cover workspace selection."""
    import application.steps as steps

    window.cover_view.inspector.title_edit.setPlainText("A title")
    window.cover_view.inspector.names_edit.setText("Two hosts")
    layout, variant, _ = window.cover_view.inspector.state()

    seen = {}
    real_build = steps.build_runners

    def capture(context, names, **kwargs):
        seen.update(context.params)
        return real_build(context, names, **kwargs)

    monkeypatch.setattr("ui.main_window.build_runners", capture)

    window._run_recipe(_result())
    if window._recipe_job is not None:
        window._recipe_job.wait(5000)
    process_events()

    assert seen["cover_layout"] == layout
    assert seen["cover_variant"] == variant
    assert seen["cover_template"] == window.cover_view.template.id
    assert seen["cover_slots"]["title"] == "A title"
    assert seen["cover_slots"]["names"] == "Two hosts"
