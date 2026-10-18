"""The persistent recipe queue: persistence, per-file recipe runs, retry and
resume (UI/UX audit 2026-10-18, UX-14 / T14).

``BatchPanel`` / ``BatchProcessor`` remain the list and the transcriber;
this controller makes them durable and recipe-aware:

* files queued get a snapshot of the recipe and transcription settings
  chosen at that moment (``domain.queue``), stored in the history database;
* when a file is transcribed it becomes a record, and the recipe's later
  steps run as a ``JobRun`` of their own, one file at a time (LM Studio
  stays at one request — ``local_llm=1`` — and a file transcribed while the
  previous one's steps run simply waits its turn);
* an item that fails or is cancelled can be retried on its own; it never
  transcribes a file twice (its record and finished steps are reused);
* after a crash, what was running is ``interrupted`` and is *shown*, never
  restarted on its own.
"""

from __future__ import annotations

import os
from collections import deque
from typing import TYPE_CHECKING, Any, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from application import queue_run, queue_store
from application.records import add_record_badges, save_new_record
from batch_processor import BatchItem, BatchStatus
from config import get_config
from core.i18n import tr
from core.logger import get_logger
from domain import queue as q
from ui.job_runner import JobRunner

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = get_logger(__name__)


class QueueController(QObject):
    """Owns the stored queue; wired to the window's BatchPanel."""

    library_changed = pyqtSignal()      # a record or run was written
    status_message = pyqtSignal(str)

    def __init__(self, host: "MainWindow") -> None:
        super().__init__(host)
        self._host = host
        self._waiting: deque[tuple[BatchItem, Any]] = deque()   # (item, result) to run steps for
        self._active: Optional[tuple[BatchItem, queue_run.FollowUp, JobRunner]] = None
        self._results: dict[int, Any] = {}   # queue_id -> TranscriptionResult for waiting items

    # ------------------------------------------------------------ wiring

    def wire(self) -> None:
        panel = self._host.batch_panel
        panel.snapshot_provider = self.snapshot_now
        panel.order_changed.connect(self._on_order_changed)
        panel.item_removed.connect(self._on_item_removed)
        panel.queue_cleared.connect(self._on_cleared)
        panel.retry_item_requested.connect(self.retry)
        proc = panel.processor
        proc.item_started.connect(self._on_item_started)
        proc.item_finished.connect(self._on_item_finished)
        proc.item_error.connect(self._on_item_error)
        proc.batch_finished.connect(self._on_batch_finished)

    def snapshot_now(self) -> q.QueueSnapshot:
        """The recipe and transcription settings in force right now."""
        from utils import get_thread_count

        host = self._host
        recipe = host._resolve_recipe(host.start_view.current_recipe_key())
        transcription = {
            "model_name": host.model_combo.currentData(),
            "language": host.language_combo.currentData(),
            "translate": host.translate_checkbox.isChecked(),
            "n_threads": get_thread_count(host.perf_combo.currentData()),
            "enable_diarization": host.diarization_checkbox.isChecked(),
            "use_gpu": host._use_gpu,
        }
        return queue_store.make_snapshot(recipe, transcription)

    # ------------------------------------------------------------ restart

    def restore(self) -> int:
        """Show what a previous session left unfinished. Crash leftovers
        become ``interrupted``; nothing starts."""
        try:
            queue_store.mark_interrupted()
            items = queue_store.load_items(unfinished_only=True)
        except Exception as exc:  # noqa: BLE001 - a broken queue must not stop the app
            logger.warning("Could not read the stored queue: %s", exc)
            return 0
        shown: list[q.QueueItem] = []
        for item in items:
            if item.snapshot is None:
                queue_store.set_state(item.id, q.FAILED, error="unreadable snapshot")
            elif not os.path.isfile(item.source_path) and item.record_id is None:
                queue_store.set_state(item.id, q.FAILED, error="source missing")
            else:
                shown.append(item)
        return self._host.batch_panel.restore_items(shown)

    # ------------------------------------------------------------ transcription side

    def _item(self, index: int) -> Optional[BatchItem]:
        items = self._host.batch_panel.processor.items
        return items[index] if 0 <= index < len(items) else None

    def _on_item_started(self, index: int) -> None:
        item = self._item(index)
        if item is not None and item.queue_id is not None:
            queue_store.set_state(item.queue_id, q.RUNNING, stage="transcribe", error="")

    def _on_item_finished(self, index: int, result: Any) -> None:
        """A file was transcribed: it becomes a record, then its recipe's
        remaining steps wait for their turn."""
        item = self._item(index)
        if item is None or result is None:
            return
        record_id = self._save_record(item, result)
        if record_id is None:
            self._finish(item, q.DONE, note=tr("queue_no_record"))
            return
        if item.queue_id is not None:
            queue_store.set_state(item.queue_id, q.RUNNING, stage="steps", record_id=record_id)
        self.library_changed.emit()
        self._enqueue_steps(item, result, record_id)

    def _on_item_error(self, index: int, error: str) -> None:
        item = self._item(index)
        if item is not None and item.queue_id is not None:
            queue_store.set_state(item.queue_id, q.FAILED, error=error[:300])

    def _on_batch_finished(self) -> None:
        """Items the batch worker cancelled are stored as cancelled (still
        retryable); pending ones stay pending for the next Start."""
        for item in self._host.batch_panel.processor.items:
            if item.queue_id is not None and item.status is BatchStatus.CANCELLED:
                stored = queue_store.load_item(item.queue_id)
                if stored is not None and stored.state in (q.RUNNING, q.PENDING):
                    queue_store.set_state(item.queue_id, q.CANCELLED)
        self._pump()

    def _save_record(self, item: BatchItem, result: Any) -> Optional[int]:
        cfg = get_config()
        own = item.transcription or {}
        return save_new_record(
            result, source_path=item.filepath,
            model=own.get("model_name") or "",
            speaker_names=getattr(result, "speaker_names", {}) or {},
            source_kind="file",
        ) if getattr(cfg, "history_enabled", True) else None

    # ------------------------------------------------------------ steps side

    def _enqueue_steps(self, item: BatchItem, result: Any, record_id: int) -> None:
        stored = queue_store.load_item(item.queue_id) if item.queue_id is not None else None
        if stored is None or stored.snapshot is None or not stored.snapshot.follow_up_steps:
            self._finish(item, q.DONE)
            return
        item.stage = tr("queue_steps_waiting")
        self._refresh(item)
        self._waiting.append((item, result))
        self._pump()

    def _pump(self) -> None:
        """Start the next waiting item's steps if none is running. One at a
        time keeps LM Studio at a single request."""
        if self._active is not None or not self._waiting:
            return
        item, result = self._waiting.popleft()
        stored = queue_store.load_item(item.queue_id) if item.queue_id is not None else None
        if stored is None or stored.record_id is None:
            self._pump()
            return
        from core.ai_provider import provider_from_config

        cfg = get_config()
        try:
            follow_up = queue_run.prepare_follow_up(
                stored, result, stored.record_id, cfg=cfg,
                provider=provider_from_config(cfg),
                insights_cache=self._host._insights_cache,
            )
        except Exception as exc:  # noqa: BLE001 - one bad item must not stop the queue
            logger.warning("Could not prepare steps for %s: %s", item.filepath, exc)
            self._finish(item, q.FAILED, note=str(exc)[:200])
            self._pump()
            return
        if follow_up is None:
            self._finish(item, q.DONE)
            self._pump()
            return
        from application.steps import build_cache_checks, build_runners

        job = JobRunner(follow_up.spec, run_state=follow_up.run)
        runners = build_runners(follow_up.context, follow_up.step_names,
                                progress_factory=job.make_progress_callback)
        job.set_runners(runners, cache_checks=build_cache_checks(
            follow_up.context, follow_up.step_names))
        job.step_started.connect(lambda name, i=item: self._on_step_started(i, name))
        job.step_progress.connect(lambda name, pct, _m, i=item: self._on_step_progress(i, name, pct))
        job.job_finished.connect(lambda run, i=item, f=follow_up: self._on_steps_finished(i, f, run))
        self._active = (item, follow_up, job)
        self._save_run(stored.id, stored.record_id, follow_up, "running")
        job.start()

    def _on_step_started(self, item: BatchItem, name: str) -> None:
        from application.steps import STEP_REGISTRY

        definition = STEP_REGISTRY.get(name)
        label = tr(definition.label_key) if definition is not None else name
        item.stage = tr("queue_step_running", step=label)
        if item.queue_id is not None:
            queue_store.set_state(item.queue_id, q.RUNNING, stage=name)
        self._refresh(item)

    def _on_step_progress(self, item: BatchItem, _name: str, percent: int) -> None:
        item.progress = percent

    def _on_steps_finished(self, item: BatchItem, follow_up: queue_run.FollowUp, run: Any) -> None:
        self._active = None
        state, error, artifact_types = queue_run.finish_run(run)
        stored = queue_store.load_item(item.queue_id) if item.queue_id is not None else None
        if stored is not None and stored.record_id is not None:
            self._save_run(stored.id, stored.record_id, follow_up, state if state != "done" else "done")
            add_record_badges(stored.record_id, artifact_types)
        self._finish(item, state, error=error)
        self.library_changed.emit()
        self._pump()

    def _save_run(self, item_id: int, record_id: int, follow_up: queue_run.FollowUp, status: str) -> None:
        """Persist the follow-up run (job_runs, the Library card's run
        composition) and remember its id on the queue item, so a retry or a
        resume continues the same run."""
        from application import run_store

        try:
            follow_up.run_id = run_store.save_run(
                record_id, follow_up.recipe_name, follow_up.run,
                run_id=follow_up.run_id, status=status,
            )
            queue_store._store().queue_update(item_id, run_id=follow_up.run_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not store the run of queue item %s: %s", item_id, exc)

    def _finish(self, item: BatchItem, state: str, *, error: str = "", note: str = "") -> None:
        if item.queue_id is not None:
            queue_store.set_state(item.queue_id, state, stage="", error=error)
        item.awaiting_follow_up = False
        if state == q.DONE:
            item.stage = note or tr("queue_item_done")
            item.follow_up_error = ""
        elif state == q.CANCELLED:
            item.stage = tr("queue_item_cancelled")
        else:
            item.stage = ""
            item.follow_up_error = self._error_text(error or note)
        self._refresh(item)

    @staticmethod
    def _error_text(error: str) -> str:
        """``"step:kind"`` from finish_run() → a sentence in the UI language."""
        step, _, kind = (error or "").partition(":")
        if kind:
            from application.steps import STEP_REGISTRY

            definition = STEP_REGISTRY.get(step)
            label = tr(definition.label_key) if definition is not None else step
            return f"{label} — {tr(f'run_failure_{kind}')}"
        return error

    def _refresh(self, item: BatchItem) -> None:
        panel = self._host.batch_panel
        try:
            index = panel.processor.items.index(item)
        except ValueError:
            return
        panel.refresh_item(index)

    # ------------------------------------------------------------ user actions

    def retry(self, index: int) -> None:
        """Retry one item. A file with a record reuses it (and its finished
        steps); otherwise it goes back to pending for the next Start —
        nothing is transcribed twice, nothing starts by itself."""
        item = self._item(index)
        if item is None or item.queue_id is None:
            if item is not None and item.status in (BatchStatus.ERROR, BatchStatus.CANCELLED):
                item.status, item.error, item.progress = BatchStatus.PENDING, None, 0
                self._refresh(item)
            return
        stored = queue_store.load_item(item.queue_id)
        if stored is None or not queue_store.retry(item.queue_id):
            return
        item.follow_up_error = ""
        if stored.record_id is not None:
            result = self._load_result(stored.record_id)
            if result is not None:
                item.status = BatchStatus.COMPLETE
                self._waiting.append((item, result))
                item.stage = tr("queue_steps_waiting")
                self._refresh(item)
                self._pump()
                return
        item.status, item.error, item.progress = BatchStatus.PENDING, None, 0
        item.stage = ""
        self._refresh(item)

    def start_backlog(self) -> int:
        """Start: items restored with a record but unfinished steps resume
        their steps now (their transcript is already saved)."""
        count = 0
        for item in self._host.batch_panel.processor.items:
            if item.awaiting_follow_up and item.queue_id is not None:
                stored = queue_store.load_item(item.queue_id)
                result = self._load_result(stored.record_id) if stored and stored.record_id else None
                item.awaiting_follow_up = False
                if result is None:
                    self._finish(item, q.FAILED, note=tr("queue_record_gone"))
                    continue
                queue_store.set_state(item.queue_id, q.RUNNING, stage="steps")
                self._waiting.append((item, result))
                count += 1
        self._pump()
        return count

    def _load_result(self, record_id: int) -> Any:
        try:
            from core.history import get_history_store
            from ui.main_window import _result_from_payload

            record = get_history_store().get_record(record_id)
            return _result_from_payload(record["payload"]) if record else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load record %s for the queue: %s", record_id, exc)
            return None

    def _on_order_changed(self, ids: list) -> None:
        try:
            queue_store.reorder([i for i in ids if i is not None])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not store the new queue order: %s", exc)

    def _on_item_removed(self, queue_id: Any) -> None:
        if queue_id is not None:
            queue_store.remove(queue_id)

    def _on_cleared(self, ids: list) -> None:
        for queue_id in ids:
            queue_store.remove(queue_id)

    def cancel(self) -> None:
        """Cancel the running steps (the batch worker's own cancel covers
        transcription); waiting items stay stored as cancelled."""
        if self._active is not None:
            item, follow_up, job = self._active
            self._active = None
            self._host._registry.retire(job)   # disconnects job_finished
            stored = queue_store.load_item(item.queue_id) if item.queue_id is not None else None
            if stored is not None and stored.record_id is not None:
                self._save_run(stored.id, stored.record_id, follow_up, "cancelled")
            self._finish(item, q.CANCELLED)
        for item, _result in list(self._waiting):
            self._finish(item, q.CANCELLED)
        self._waiting.clear()

    def is_busy(self) -> bool:
        return self._active is not None or bool(self._waiting)

    def shutdown(self) -> None:
        """Shutdownable: stop the running steps. Whatever was unfinished is
        stored as ``interrupted`` (offered again next launch, not resumed)."""
        if self._active is not None:
            item, _follow_up, job = self._active
            self._active = None
            try:
                job.job_finished.disconnect()
            except TypeError:
                pass
            job.cancel()
            if not job.wait(3000):
                self._host._registry.register(job, name="queue_steps")
                self._host._registry.retire(job)
            if item.queue_id is not None:
                queue_store.set_state(item.queue_id, q.INTERRUPTED)
        for item, _result in self._waiting:
            if item.queue_id is not None:
                queue_store.set_state(item.queue_id, q.INTERRUPTED)
        self._waiting.clear()
