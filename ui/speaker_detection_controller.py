"""Speaker detection for a transcript that already exists.

The recipe's "diarize" step (``application/steps.py``) labels a saved
record, a Live result or a run where diarization was off. This controller
runs that step on its own for the open record ("Detect Speakers" in the
Transcribe menu and the command palette) and is also where a recipe run's
diarize result lands (``MainWindow._show_step_result`` for the
"transcript" viewer): the labelled transcript goes through
``DocumentSession.apply_result`` and is saved as a new version of the
record. The window stays the host: it owns the panels, the status line
and the ``WorkerRegistry`` this controller registers with.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from application.records import save_record_version
from application.steps import (
    StepContext,
    build_job_spec,
    build_step_context,
    has_speakers,
    step_outcome_result,
)
from core.i18n import tr, tr_count
from core.logger import get_logger
from domain.job import StepStatus
from domain.transcription import TranscriptionResult
from ui.job_runner import JobRunner
from ui.toast import show_toast

if TYPE_CHECKING:
    from application.job_engine import JobRun
    from ui.main_window import MainWindow

logger = get_logger(__name__)

_JOB_WAIT_MS = 5000


def _texts(result: TranscriptionResult) -> list:
    """The transcript's text and timing, without speakers — what must not
    have changed for a labelled copy to replace the open transcript."""
    return [(seg.start, seg.end, seg.text) for seg in result.segments]


class SpeakerDetectionController:
    """Run the diarize step for the open record and apply its result."""

    def __init__(self, host: "MainWindow") -> None:
        self._host = host
        self.job: Optional[JobRunner] = None
        self._context: Optional[StepContext] = None
        self._record_id: Optional[int] = None

    def is_running(self) -> bool:
        return self.job is not None and self.job.isRunning()

    # ------------------------------------------------------------ start

    def source_path(self) -> Optional[str]:
        """The open record's media, if it is still on disk."""
        host = self._host
        for path in (host._record_source_path, host._source_filepath):
            if path and Path(path).is_file():
                return path
        return None

    def start(self) -> None:
        """Detect speakers in the open transcript (menu / palette action)."""
        host = self._host
        result = host._current_result
        if result is None:
            show_toast(host, tr("speakers_no_transcript"), kind="info")
            return
        if self.is_running():
            show_toast(host, tr("speakers_busy"), kind="info")
            return
        if has_speakers(result):
            show_toast(host, tr("speakers_already"), kind="info")
            return
        source = self.source_path()
        if source is None:
            show_toast(host, tr("speakers_no_source"), kind="error", duration_ms=5000)
            return
        import diarizer

        if not diarizer.is_installed():
            show_toast(host, tr("speakers_unavailable"), kind="error", duration_ms=5000)
            return

        job = JobRunner(build_job_spec("diarize-only", ("diarize",)))
        self._context = build_step_context(
            source,
            result,
            host._last_record_id,
            artifact_source=host._artifact_source(),
            is_cancelled=job.run_state.is_cancelled,
        )
        self._record_id = host._last_record_id
        self.job = job
        host.status_label.setText(tr("speakers_running"))
        host._launch_step_job(job, "diarize", self._context, self._on_progress, self._on_finished)

    def _on_progress(self, _name: str, _percentage: int, message: str) -> None:
        self._host.status_label.setText(message)

    def _on_finished(self, run: "JobRun") -> None:
        host = self._host
        context, record_id = self._context, self._record_id
        self.job = None
        self._context = None
        self._record_id = None
        result, error = step_outcome_result(run, "diarize", context, TranscriptionResult)
        if result is None:
            outcome = run.outcomes.get("diarize")
            if outcome is not None and outcome.status is StepStatus.CANCELLED:
                host.status_label.setText("")
                return
            host.status_label.setText(tr("speakers_failed", error=error[:80]))
            show_toast(host, tr("speakers_failed", error=error), kind="error", duration_ms=6000)
            return
        if not has_speakers(result):
            host.status_label.setText(tr("speakers_none_found"))
            show_toast(host, tr("speakers_none_found"), kind="info")
            return
        self.apply(result, record_id)

    # ------------------------------------------------------------ apply

    def apply(self, result: Any, record_id: Optional[int]) -> bool:
        """Show a labelled transcript for *record_id* and save it as a new
        version. Returns True when it was applied.

        Used for this controller's own job and for a recipe run's diarize
        step. A pass-through (the open transcript itself, already labelled
        during transcription) or a result without speakers changes nothing. The result is dropped when
        another record is open now, or when the open transcript's text was
        edited while the diarizer ran — applying it would undo the edit.
        Renames the user made meanwhile are kept.
        """
        host = self._host
        if not isinstance(result, TranscriptionResult):
            return False
        current = host._current_result
        if result is current or current is None:
            return False
        if record_id != host._last_record_id:
            logger.info("Speakers for record %s arrived after another record was opened", record_id)
            return False
        if not has_speakers(result):
            return False
        if _texts(current) != _texts(result):
            show_toast(host, tr("speakers_stale"), kind="warning", duration_ms=5000)
            return False
        result.speaker_names = {**(result.speaker_names or {}), **(current.speaker_names or {})}
        host.transcript_view.set_result(result)
        host._document_session.apply_result(result)
        if save_record_version(record_id, result):
            host.library_view.refresh()
        count = len({seg.speaker for seg in result.segments if seg.speaker})
        message = tr_count("speakers_found", count)
        host.status_label.setText(message)
        show_toast(host, message, kind="success")
        return True

    # ------------------------------------------------------------ shutdown

    def cancel(self) -> None:
        """Stop a running detection without blocking; a job that is still
        running is retired through the registry, not dropped."""
        job = self.job
        if job is None or not job.isRunning():
            return
        self._host._registry.retire(job)
        self.job = None
        self._context = None
        self._record_id = None

    def shutdown(self) -> None:
        """Shutdownable (ui/shutdownable.py): cancel a running detection;
        one that outlives the bounded wait is retired, not dropped."""
        job = self.job
        if job is None or not job.isRunning():
            return
        job.cancel()
        if not job.wait(_JOB_WAIT_MS):
            logger.warning("Speaker detection did not stop in time; deferring to WorkerRegistry")
            self._host._registry.register(job, name="speaker_detection")
            self._host._registry.retire(job)
