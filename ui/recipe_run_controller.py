"""A recipe run: launch, resume, retry, cancel and the run's bookkeeping.

Moved out of ``MainWindow``: after a fresh transcription (or a "Resume" on
a Library card) it runs the rest of the selected recipe's steps as one
``JobRunner`` (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B6), feeds the run
screen, persists the run to ``job_runs`` (B8) and reports how it ended.
What the steps do lives in ``application/steps.py`` and the history writes
in ``application/records.py``/``application/run_store.py``; the window
stays the host — it owns the panels a finished step's result lands in, the
status line, recipe selection and the ``WorkerRegistry`` the runner is
retired through.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Optional

from PyQt6.QtCore import QTimer

from application.job_engine import JobRun
from application.records import add_record_badges
from application.steps import (
    STEP_REGISTRY,
    StepContext,
    build_cache_checks,
    build_job_spec,
    build_runners,
    build_step_context,
    llm_params,
    load_step_result,
    manifest_path_for_step,
    summarize_run,
)
from config import get_config
from core.i18n import tr
from core.logger import get_logger
from domain.job import JobSpec, StepOutcome, StepStatus
from domain.transcription import TranscriptionResult
from ui.job_runner import JobRunner
from ui.option_labels import recipe_label
from ui.toast import show_toast

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = get_logger(__name__)

_JOB_WAIT_MS = 5000


class RecipeRunController:
    """The window's recipe run (see module docstring)."""

    def __init__(self, host: "MainWindow") -> None:
        self._host = host
        # The running JobRunner, None when idle. Separate from the window's
        # five single-step _*_job attributes, which stay as each panel's
        # own direct re-run/retry path.
        self.job: Optional[JobRunner] = None
        # The current run's spec/state/context, kept around so a RunView
        # retry can rebuild a fresh JobRunner against the same JobRun
        # instead of starting the whole run over.
        self.job_run: Optional[JobRun] = None
        self._spec: Optional[JobSpec] = None
        self._step_names: tuple = ()
        self._context: Optional[StepContext] = None
        # The job_runs row id for the run above, once persisted (B8, see
        # application/run_store.py) — None until there's a real history
        # record to attach it to.
        self._run_id: Optional[int] = None
        # The record the current run belongs to — fixed when the run
        # starts. The user may open another record while it runs; its
        # results, run row and badges must still go to this one.
        self.record_id: Optional[int] = None

    def wire(self) -> None:
        """Connect the host's run screen to this controller."""
        view = self._host.run_view
        view.retry_requested.connect(self.retry)
        view.regenerate_requested.connect(self.regenerate)
        view.overall_progress_changed.connect(self.on_overall_progress)
        view.cancel_requested.connect(self.cancel)

    def is_running(self) -> bool:
        return self.job is not None and self.job.isRunning()

    # ------------------------------------------------------------ start

    def _build_context(self, result: TranscriptionResult, record_id: Any, run: JobRun) -> StepContext:
        """The StepContext every step of *run* reads — the window's current
        source, generation settings and Cover workspace."""
        from core.ai_provider import provider_from_config

        host = self._host
        cfg = get_config()
        return build_step_context(
            host._source_filepath or "",
            result,
            record_id,
            artifact_source=host._artifact_source(),
            params=llm_params(
                cfg,
                result,
                provider=provider_from_config(cfg),
                # Shared with InsightsPanel/YouTubePanel so a type more
                # than one step generates (e.g. "chapters") isn't
                # recomputed — see core/insights_cache.py.
                insights_cache=host._insights_cache,
                do_unwrap=host.book_panel.chk_unwrap.isChecked(),
                do_custom=host.book_panel.chk_custom.isChecked(),
                custom_prompt_path=host.book_panel.custom_prompt_edit.text().strip(),
                # The "YouTube video" recipe includes the cover step, so it
                # has to render what the Cover workspace is actually set to
                # rather than _cover_runner's own fallback defaults.
                **host.cover_view.render_params(),
            ),
            get_result=lambda name: self._get_result(run, name),
            is_cancelled=run.is_cancelled,
        )

    def start(self, result: TranscriptionResult, show_run_screen: bool = True) -> None:
        """After a fresh transcription, run the rest of the selected
        recipe's steps as one JobRunner (B6) — replaces the old preset
        chain's one-button-click-per-step orchestration now that every
        generator is a job-engine step (B5). *show_run_screen* is False
        for a live-finished/batch result (the window's _on_finished
        open_record=False callers): the run still executes, it just
        doesn't yank the view away from wherever the user already is."""
        host = self._host
        recipe = host._resolve_recipe(host.start_view.current_recipe_key())
        spec = recipe.to_job_spec(build_job_spec)
        step_names = tuple(step.name for step in spec.steps)

        # A live session (B7, docs/UI_REDESIGN_PLAN_2026-09.ru.md) already
        # produced this result by streaming, not by running the job-engine
        # "transcribe"/"diarize" steps — SKIPPED (not SUCCEEDED) is the
        # honest status for a step this run never actually executed, and
        # matches how a real cache-skip renders on the run screen.
        already_streamed = host._source_kind == "live"
        transcribe_status = StepStatus.SKIPPED if already_streamed else StepStatus.SUCCEEDED
        run = JobRun(spec=spec)
        if "transcribe" in step_names:
            run.outcomes["transcribe"] = StepOutcome(
                "transcribe", transcribe_status, result=result
            )
        if "diarize" in step_names and any(seg.speaker for seg in result.segments):
            run.outcomes["diarize"] = StepOutcome(
                "diarize", transcribe_status, result=result
            )

        record_id = host._last_record_id if host._last_record_id is not None else "unsaved"
        self.job_run = run
        self._spec = spec
        self._step_names = step_names
        self._context = self._build_context(result, record_id, run)
        self._run_id = None
        self.record_id = host._last_record_id
        host.run_view.bind_run(run)
        host.run_view.set_recipe_name(recipe_label(recipe))
        host.run_view.set_finished(False)
        host.run_view.set_publish_available(False)
        if show_run_screen:
            host._stack.setCurrentIndex(host._run_index)
        self._save("running")
        self._launch()

    def resume(self, record_id: int) -> None:
        """LibraryView.resume_run (B2, docs/IMPROVEMENT_PLAN_2026-08.ru.md):
        pick up a run that stopped short — failed, or was interrupted by
        a crash (run_store.mark_stale_running_as_interrupted) — from
        wherever it left off.

        Needs B1: a restored StepOutcome's result is always None (see
        run_store's own module docstring), so a dependent step reads a
        SUCCEEDED/SKIPPED predecessor's real output from disk via
        load_step_result(), exactly like a cache-skipped step already
        does. Resumes by the run's *saved* step composition (its own
        recipe, resolved by name) rather than assuming nothing changed —
        the recipe could have been edited since (B4 makes that easy) or
        deleted outright, in which case the window's _resolve_recipe()
        already falls back to transcript-only and the mismatch note below
        explains why the run screen looks different from what actually ran.
        """
        from application import run_store

        host = self._host
        if not host._load_from_history(record_id):
            return
        stored = run_store.load_latest_run(record_id)
        if stored is None:
            return
        result = host._current_result
        if result is None:
            return

        recipe = host._resolve_recipe(stored.recipe)
        spec = build_job_spec(stored.recipe, recipe.steps)
        step_names = tuple(step.name for step in spec.steps)
        run = JobRun(spec=spec)
        run_store.apply_stored_outcomes(run, stored)

        self.job_run = run
        self._spec = spec
        self._step_names = step_names
        self._context = self._build_context(result, record_id, run)
        self._run_id = stored.id
        self.record_id = record_id
        host.run_view.bind_run(run)
        name = recipe_label(recipe)
        if set(stored.outcomes) - set(recipe.steps):
            name = tr("run_resumed_mismatch", name=name)
        host.run_view.set_recipe_name(name)
        host.run_view.set_finished(False)
        host.run_view.set_publish_available(False)
        host._stack.setCurrentIndex(host._run_index)
        # JobEngine never re-resolves a step already in run.outcomes, so
        # _launch() below won't fire step_finished for any of these
        # restored SUCCEEDED/SKIPPED outcomes — without this loop their
        # tabs (Clean, Article, ...) would stay empty until the user
        # happened to trigger some other refresh.
        for step_name, outcome in run.outcomes.items():
            if outcome.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED):
                self.on_step_finished(step_name, outcome)
        self._save("running")
        self._launch()

    def _get_result(self, run: JobRun, name: str):
        """``StepContext.get_result`` for the recipe run: a finished
        step's real return value, or — for a step JobEngine resolved via
        cache-skip (``StepOutcome.result`` is ``None`` on a SKIPPED
        outcome, since the runner that would have produced it never ran)
        — its artifact reloaded from disk (see
        application/steps.py::load_step_result and
        docs/IMPROVEMENT_PLAN_2026-08.ru.md, B1). Without this, a step
        that reads a cache-skipped dependency (e.g. "article" reading
        "clean") would see None and treat it as though that dependency
        had never run at all.
        """
        outcome = run.outcomes.get(name)
        if outcome is None:
            return None
        if outcome.result is not None:
            return outcome.result
        if self._context is None:
            return None
        return load_step_result(self._context, name)

    def _save(self, status: str) -> None:
        """Persist the current run's outcomes to job_runs (B8, see
        application/run_store.py) — a no-op until there's a real history
        record to attach the run to. Reuses the run's row id across calls
        so this updates one row instead of inserting a new one for every
        step/retry."""
        if self.record_id is None or self.job_run is None or self._spec is None:
            return
        from application import run_store

        try:
            self._run_id = run_store.save_run(
                self.record_id, self._spec.name, self.job_run,
                run_id=self._run_id, status=status,
            )
        except Exception as exc:
            logger.warning("Failed to persist recipe run: %s", exc)

    def _launch(self) -> None:
        """(Re)start the current run's JobRunner against whatever steps
        ``self.job_run`` doesn't already have an outcome for — used by
        start(), resume() and retry() alike, since JobEngine.run() only
        (re)runs steps missing from run_state.outcomes (see
        application/job_engine.py)."""
        if self._spec is None or self._context is None:
            return
        host = self._host
        job = JobRunner(self._spec, run_state=self.job_run)
        self.job = job
        runners = build_runners(
            self._context, self._step_names,
            progress_factory=job.make_progress_callback,
        )
        cache_checks = build_cache_checks(self._context, self._step_names)
        job.set_runners(runners, cache_checks=cache_checks)
        job.step_started.connect(host.run_view.on_step_started)
        job.step_progress.connect(host.run_view.on_step_progress)
        job.step_finished.connect(host.run_view.on_step_finished)
        job.step_finished.connect(self.on_step_finished)
        job.job_finished.connect(self.on_job_finished)
        host.cancel_btn.setVisible(True)
        host.transcribe_btn.setEnabled(False)
        host.status_label.setText(tr("status_chain_running"))
        job.start()
        host._refresh_run_chip()

    # ------------------------------------------------------------ run screen

    def cancel(self) -> None:
        """Cancel the current run without blocking the GUI thread.

        Mirrors the window's five single-step ``_cancel_*_job`` methods:
        disconnects business signals immediately (so a late
        step/job-finished can't reach UI state that already believes the
        run was cancelled) and hands it to WorkerRegistry, which deletes
        it once its QThread actually finishes.

        Reporting the cancellation lives here rather than in the status
        bar's own handler: retiring the runner disconnects job_finished,
        so on_job_finished never runs and nothing else takes the screen
        out of its "running" state. A run cancelled from a step row's own
        Cancel button used to leave "Running the recipe…" on the status
        bar, the Cancel button still offering to cancel it, and the run
        screen with no way out.
        """
        if self.job is None or not self.job.isRunning():
            return
        host = self._host
        host._registry.retire(self.job)
        self.job = None
        self._save("cancelled")
        host.library_view.refresh()
        host._refresh_run_chip()
        host.status_label.setText(tr("status_chain_cancelled"))
        host._reset_ui()
        host.run_view.set_finished(True)

    def retry(self, name: str) -> None:
        """RunView.retry_requested: it has already reset *name* (and any
        dependent step only CANCELLED because of it) on the same JobRun
        we're about to reuse — see RunView._on_retry."""
        if self._context is None or self.job_run is None or self.is_running():
            return
        if self.job_run.is_cancelled():
            self.job_run = self._run_after_cancel(self.job_run, self._context)
        self._save("running")
        self._launch()

    def regenerate(self, name: str) -> None:
        """RunView.regenerate_requested: same reused-run mechanics as
        retry(), plus deleting *name*'s on-disk manifest first — RunView
        already reset the step's outcome, but a still-valid cache would
        otherwise just re-mark it SKIPPED with the same old result (B1's
        "forced regeneration": revision/hash/prompt-version already
        invalidate the cache on their own; wanting a different result from
        unchanged inputs is the one case only this manual action covers)."""
        if self._context is None or self.job_run is None or self.is_running():
            return
        manifest = manifest_path_for_step(self._context, name)
        if manifest is not None and manifest.exists():
            try:
                manifest.unlink()
            except OSError as exc:
                logger.warning("Failed to delete manifest for regenerate (%s): %s", name, exc)
        if self.job_run.is_cancelled():
            self.job_run = self._run_after_cancel(self.job_run, self._context)
        self._save("running")
        self._launch()

    def _run_after_cancel(self, cancelled: JobRun, context: StepContext) -> JobRun:
        """A fresh JobRun carrying the cancelled one's outcomes forward.

        ``JobRun.cancel()`` latches a threading.Event that nothing clears,
        and every step resolves through it (``JobEngine._resolve_step``
        marks a step CANCELLED before running it, and each runner's
        ``StepContext.is_cancelled`` is bound to it) — so retrying a step
        on a run that was ever cancelled would immediately re-resolve it
        CANCELLED without running anything at all.

        Clearing the flag in place is not the fix: cancel() retires the
        JobRunner rather than blocking on it (see its docstring), so the
        cancelled worker may still be inside a step that is watching this
        very flag, and un-cancelling underneath it would let it resume and
        race the retry. A new JobRun leaves that worker with the old,
        still-cancelled one it already holds.
        """
        fresh = JobRun(spec=cancelled.spec)
        fresh.outcomes.update(cancelled.outcomes)
        self._context = dataclasses.replace(
            context,
            get_result=lambda name: self._get_result(fresh, name),
            is_cancelled=fresh.is_cancelled,
        )
        self._host.run_view.bind_run(fresh)
        return fresh

    def on_overall_progress(self, percent: int) -> None:
        """RunView.overall_progress_changed (B3): mirrors the run screen's
        own N-of-M bar into the persistent status bar, so the overall
        percentage is visible without switching to the run screen. Guarded
        on an actually-running recipe job so a RunView recompute from
        something else (a fixture bind, a retried/regenerated step's
        reset) never overwrites an unrelated status-bar operation."""
        if not self.is_running():
            return
        self._host.status_bar.set_operation(tr("status_chain_running"), progress=percent)

    # ------------------------------------------------------------ results

    def on_step_finished(self, name: str, outcome: StepOutcome) -> None:
        """Feed a just-finished recipe step's result to whichever tab
        shows it. Mirrors the success branch of each single-step
        _on_*_job_finished in the window (this run and those five
        buttons' own jobs share application/steps.py's runners; only
        where the result lands differs) — transcribe/diarize/cover have no
        tab to push into, so they're left to the run screen's own row
        status.

        A SKIPPED step (cache hit — B1) never ran its runner, so
        outcome.result is None; so does every restored outcome a resumed
        run (B2) feeds through here, regardless of its own status —
        run_store's contract is that a StepOutcome read back from storage
        always has result=None (see application/run_store.py's module
        docstring). Either way, the tab still needs populating from the
        artifact already on disk via load_step_result(), the same
        reconstruction _get_result() uses to feed dependent steps.
        """
        host = self._host
        self._save("running")
        # The Library card shows the run in progress and, once a step
        # fails, which one.
        host.library_view.refresh()
        if self.record_id != host._last_record_id:
            # Another record is open now. The result is on disk in the
            # run's own record folder and comes back when that record is
            # opened; it must not land in this record's tabs.
            return
        if outcome.status is StepStatus.FAILED:
            # Only these two panels have a set_error()/retry affordance of
            # their own (ui/youtube_panel.py, ui/insights_panel.py) — a
            # failed "clean"/"article"/"book" step is still visible on the
            # run screen's own row, same as before this branch existed.
            viewer = STEP_REGISTRY[name].viewer
            if viewer == "youtube":
                host.youtube_panel.set_error(outcome.error)
            elif viewer == "insights":
                host.insights_panel.set_error(outcome.error)
            return
        if outcome.status not in (StepStatus.SUCCEEDED, StepStatus.SKIPPED):
            return
        result = outcome.result
        if result is None and self._context is not None:
            result = load_step_result(self._context, name)
        host._show_step_result(STEP_REGISTRY[name].viewer, result)

    def on_job_finished(self, run: JobRun) -> None:
        """Persist the finished run (B8, job_runs — the Library card's run
        composition) and whichever steps actually produced an artifact to
        this record's history badges (mirroring the old preset chain's
        own bookkeeping), and report how many came out of it."""
        host = self._host
        self.job = None
        host._reset_ui()
        host.run_view.set_finished(True)

        summary = summarize_run(run)
        succeeded, had_error = summary.succeeded, summary.had_error
        artifact_types = summary.artifact_types
        self._save("failed" if had_error else "done")
        host.library_view.refresh()
        host._refresh_run_chip()
        add_record_badges(self.record_id, artifact_types)

        # _reset_ui() only hides the progress/cancel affordances — without
        # this the one persistent status line would keep reading "Running
        # the recipe…" long after the run ended, since no other call site
        # writes to it until the next operation starts.
        if had_error:
            host.status_label.setText(tr("status_chain_failed"))
        else:
            host.status_label.setText(
                tr("status_chain_done", count=len(artifact_types))
            )

        if had_error:
            show_toast(host, tr("toast_chain_error"), kind="error")
        elif artifact_types:
            show_toast(
                host, tr("toast_chain_done", count=len(artifact_types)), kind="success"
            )

        # Publishing reads the YouTube tab — only the run's own record's.
        can_publish = (
            "youtube_package" in succeeded and self.record_id == host._last_record_id
        )
        host.run_view.set_publish_available(can_publish)
        if can_publish and get_config().yt_publish_mode != "off":
            QTimer.singleShot(0, host.youtube_publish.open_dialog)

    def shutdown(self) -> None:
        """Shutdownable (ui/shutdownable.py): cancel a running run; one
        that outlives the bounded wait is retired, not dropped."""
        job = self.job
        if job is None or not job.isRunning():
            return
        job.cancel()
        if not job.wait(_JOB_WAIT_MS):
            logger.warning("Recipe run did not stop in time; deferring to WorkerRegistry")
            self._host._registry.register(job, name="recipe_run")
            self._host._registry.retire(job)
