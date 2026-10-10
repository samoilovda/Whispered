"""A Live session from preflight to its last checkpoint.

Moved out of ``MainWindow``: it runs the preflight worker, starts, pauses
and stops ``LiveRuntime``, feeds segment updates to the Live view and saves
the finalized text to history as the meeting goes. What happens with the
finished transcript (opening it as the current document) stays with the
window, which hosts the Live view, the runtime and the Library.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Optional

from config import get_config
from core.live.contracts import SegmentState
from core.live.preflight import default_helper_path
from core.logger import get_logger
from ui.live_checkpoint_tracker import LiveCheckpointTracker
from ui.live_preflight_panel import LivePreflightWorker

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = get_logger(__name__)

_PREFLIGHT_WAIT_MS = 2500


class LiveSessionController:
    """Live preflight, session control and checkpoints (see module docstring)."""

    def __init__(self, host: "MainWindow") -> None:
        self._host = host
        # Finalized live text, saved to history while the meeting runs.
        self.checkpoint = LiveCheckpointTracker()
        self._preflight_worker: Optional[LivePreflightWorker] = None

    def wire(self) -> None:
        """Connect the host's Live view and runtime to this controller."""
        view, runtime = self._host.live_view, self._host.live_runtime
        view.preflight_requested.connect(self.run_preflight)
        view.start_requested.connect(self.start)
        view.pause_requested.connect(self.pause)
        view.stop_requested.connect(self.stop)
        view._timer.timeout.connect(self.update_metrics)
        runtime.segment_update.connect(self.on_segment_update)
        runtime.error_occurred.connect(self.on_error)

    def _options(self) -> tuple[bool, bool, Any]:
        use_mic, use_system = self._host.live_view.selected_sources()
        return use_mic, use_system, self._host.live_view.selected_model()

    def run_preflight(self) -> None:
        if self._preflight_worker is not None and self._preflight_worker.isRunning():
            return
        view = self._host.live_view
        use_mic, use_system, model = self._options()
        view.set_preflighting()
        worker = LivePreflightWorker(
            use_mic=use_mic,
            use_system=use_system,
            model_name=model,
            target_available=not use_system or view.selected_target() is not None,
            helper_path=default_helper_path(),
            parent=self._host,
        )
        worker.completed.connect(view.show_preflight)
        self._preflight_worker = worker
        worker.start()

    def start(self) -> None:
        host = self._host
        view = host.live_view
        use_mic, use_system, model = self._options()
        discovered = view.selected_target()
        if use_system and discovered is None:
            view.invalidate_preflight()
            return
        # A missing model downloads here, verified, before the session
        # starts — not unverified inside the live worker process.
        if not host._ensure_whisper_model(model):
            return
        view.reset_session()
        self.checkpoint.start(
            source_name=time.strftime("Live %Y-%m-%d %H:%M"),
            model_name=model,
        )
        started = host.live_runtime.start(
            use_mic=use_mic,
            use_system=use_system,
            model_name=model,
            language=view.selected_language(),
            mic_device=view.selected_mic_device(),
            target=discovered.capture_target() if discovered else None,
            helper_path=default_helper_path(),
        )
        if started:
            view._timer.start()

    def pause(self, paused: bool) -> None:
        if paused:
            self._host.live_runtime.pause()
        else:
            self._host.live_runtime.resume()

    def stop(self) -> None:
        self._host.live_runtime.stop()

    def update_metrics(self) -> None:
        runtime = self._host.live_runtime
        if runtime.session_state.value not in {"idle", "completed"}:
            self._host.live_view.set_metrics(runtime.metrics())

    def on_error(self, source: str, message: str) -> None:
        self._host.live_view.set_source_state(source, "failed")
        logger.error("Live %s failure: %s", source, message)

    def on_segment_update(self, update) -> None:
        """Render an update and checkpoint only immutable live text."""
        self._host.live_view.accept_update(update)
        if update.state is not SegmentState.FINAL:
            return
        self.checkpoint.accept_final(update.segment_id, update.segment)
        self._checkpoint_history()

    def _checkpoint_history(self) -> None:
        """Persist finalized text during a meeting without writing audio."""
        if not getattr(get_config(), "history_enabled", True):
            return
        host = self._host
        try:
            from core.history import get_history_store

            wrote = self.checkpoint.checkpoint(
                get_history_store(), host.live_view.selected_language()
            )
            if wrote:
                host._last_record_id = self.checkpoint.history_record_id
                host.library_view.refresh()
        except Exception as exc:  # noqa: BLE001 - the session keeps running
            logger.warning("Failed to checkpoint live transcript: %s", exc)

    def shutdown(self) -> None:
        """Shutdownable (ui/shutdownable.py): stop a running preflight; one
        that outlives the bounded wait is retired, not dropped."""
        worker = self._preflight_worker
        if worker is None or not worker.isRunning():
            return
        worker.cancel()
        if not worker.wait(_PREFLIGHT_WAIT_MS):
            logger.warning("Live preflight did not stop in time; deferring to WorkerRegistry")
            self._host._registry.register(worker, name="live_preflight")
            self._host._registry.retire(worker)
