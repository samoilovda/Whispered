"""Real-Qt test for MainWindow's "Clean" button, now routed through
application/steps.py's "clean" step via JobRunner instead of the generic
AIProcessingWorker (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B5a).
"""

from __future__ import annotations

from transcriber import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[Segment(0.0, 1.0, "um so anyway hello there")],
        language="en",
        duration=1.0,
    )


class _FakeLMClient:
    is_cancelled = None


class _FakeTextProcessor:
    """Matches tests/test_steps.py's fake — same shape _clean_runner needs
    (``lm_client.is_cancelled`` must be settable)."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.lm_client = _FakeLMClient()

    def process(self, raw_text, use_ai=True, on_progress=None):
        from text_processor import CleanedText, CoherentText, ProcessingResult

        if on_progress is not None:
            on_progress(50, "cleaning...")
        return ProcessingResult(
            original=raw_text,
            cleaned=CleanedText(raw_text, raw_text, 0, 0, 0),
            coherent=CoherentText("Cleaned and coherent text."),
        )


def test_clean_button_runs_through_job_runner_and_writes_provenance(
    monkeypatch, process_events,
):
    monkeypatch.setattr("text_processor.TextProcessor", _FakeTextProcessor)
    from ui.main_window import MainWindow

    window = MainWindow()
    window._document_session.apply_result(_result())
    window._last_record_id = None
    window._source_filepath = None

    window._start_text_cleaning()
    runner = window._clean_job
    assert runner is not None

    assert runner.wait(2000)
    process_events()

    assert window._clean_job is None
    assert window._cleaned_text == "Cleaned and coherent text."
    assert window.cleaned_view._cleaned_text == "Cleaned and coherent text."

    from core.paths import artifact_dir
    manifest = artifact_dir("unsaved", "recording") / "clean.md.manifest.json"
    assert manifest.exists()
    assert manifest.read_text(encoding="utf-8").strip()

    window.close()


def test_second_clean_click_on_the_same_input_skips_and_reuses_the_artifact(
    monkeypatch, process_events,
):
    """B1 cache-skip: a second "Clean" click with an unchanged transcript
    and params must not re-run TextProcessor at all — it should read
    clean.md straight off disk via application/steps.py::_clean_load()."""
    from domain.job import StepStatus

    monkeypatch.setattr("text_processor.TextProcessor", _FakeTextProcessor)
    from ui.main_window import MainWindow

    window = MainWindow()
    window._document_session.apply_result(_result())
    window._last_record_id = None
    window._source_filepath = None

    window._start_text_cleaning()
    assert window._clean_job.wait(2000)
    process_events()
    assert window._cleaned_text == "Cleaned and coherent text."

    class _ExplodingTextProcessor(_FakeTextProcessor):
        def process(self, raw_text, use_ai=True, on_progress=None):
            raise AssertionError("cache hit should never call TextProcessor.process()")

    monkeypatch.setattr("text_processor.TextProcessor", _ExplodingTextProcessor)
    window._cleaned_text = None
    window.cleaned_view.set_text("")

    window._start_text_cleaning()
    runner = window._clean_job
    assert runner is not None
    assert runner.wait(2000)
    process_events()

    assert window._clean_job is None
    assert runner.run_state.outcomes["clean"].status is StepStatus.SKIPPED
    assert window._cleaned_text == "Cleaned and coherent text."
    assert window.cleaned_view._cleaned_text == "Cleaned and coherent text."

    window.close()


def test_clean_job_failure_reports_the_error_without_crashing(
    monkeypatch, process_events,
):
    class _FailingTextProcessor(_FakeTextProcessor):
        def process(self, raw_text, use_ai=True, on_progress=None):
            raise RuntimeError("LM Studio unreachable")

    monkeypatch.setattr("text_processor.TextProcessor", _FailingTextProcessor)
    from PyQt6.QtWidgets import QMessageBox
    from ui.main_window import MainWindow

    # QMessageBox.warning() is modal — under the offscreen QPA there is
    # nothing to dismiss it, so it would block this test forever. Stub it
    # the same way a real user's click would resolve it, and just check
    # it was actually shown.
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args, **kwargs: warnings.append(args) or QMessageBox.StandardButton.Ok,
    )

    window = MainWindow()
    window._document_session.apply_result(_result())
    window._last_record_id = None
    window._source_filepath = None

    window._start_text_cleaning()
    runner = window._clean_job
    assert runner is not None
    assert runner.wait(2000)
    process_events()

    assert window._clean_job is None
    assert window._cleaned_text is None
    assert "LM Studio unreachable" in window.status_label.text()
    assert warnings

    window.close()


def test_cancel_clean_job_stops_the_worker_without_reporting_a_result(
    monkeypatch, process_events,
):
    import threading

    release = threading.Event()
    started = threading.Event()

    class _BlockingTextProcessor(_FakeTextProcessor):
        def process(self, raw_text, use_ai=True, on_progress=None):
            started.set()
            release.wait(5)
            if self.lm_client.is_cancelled():
                raise RuntimeError("cancelled")
            return super().process(raw_text, use_ai=use_ai, on_progress=on_progress)

    monkeypatch.setattr("text_processor.TextProcessor", _BlockingTextProcessor)
    from ui.main_window import MainWindow

    window = MainWindow()
    window._document_session.apply_result(_result())
    window._last_record_id = None
    window._source_filepath = None

    window._start_text_cleaning()
    runner = window._clean_job
    assert runner is not None
    assert started.wait(2)

    window._cancel_clean_job()
    assert window._clean_job is None
    release.set()
    assert runner.wait(2000)
    process_events()

    assert window._cleaned_text is None

    window.close()
