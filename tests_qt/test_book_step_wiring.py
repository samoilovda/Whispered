"""Real-Qt test for BookPanel's "Run" button (run_single_requested ->
MainWindow._start_book_job), now routed through application/steps.py's
"book" step via JobRunner instead of AIProcessingWorker's "book_unwrap"
action (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B5e — the same migration
B5a/B5b/B5c/B5d did for clean/article/insights/youtube_package).

Unlike the other four generators, BookPanel never created its own worker
for a single-file run (only its now-hidden folder-batch section does,
untouched by this migration) — run_single_requested already just asked
MainWindow to do it, so there's no panel-side worker-removal surgery
here, only the MainWindow side (mirrors B5a's clean migration almost
exactly).
"""

from __future__ import annotations

from transcriber import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[Segment(0.0, 1.0, "um so anyway hello there")],
        language="en",
        duration=1.0,
    )


class _FakeBookPipeline:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def process(self, transcript_text, source_path, **kwargs):
        from book_pipeline import BookResult, BookStageResult

        on_progress = kwargs.get("on_progress")
        if on_progress is not None:
            on_progress(50, "unwrapping...")
        return BookResult(
            source_path=source_path,
            stages=[BookStageResult(
                stage="unwrap", output_text="Final book text.",
                output_path=str(kwargs.get("output_dir", ".")) + "/book_unwrap.md",
            )],
        )


def test_run_button_runs_through_job_runner_and_writes_provenance(
    monkeypatch, process_events,
):
    monkeypatch.setattr("book_pipeline.BookPipeline", _FakeBookPipeline)
    from ui.main_window import MainWindow

    window = MainWindow()
    window._document_session.apply_result(_result())
    window._last_record_id = None
    window._source_filepath = None

    window.book_panel.run_single_requested.emit(True, False, "")
    runner = window._book_job
    assert runner is not None

    assert runner.wait(2000)
    process_events()

    assert window._book_job is None
    assert "Final book text." in window.cleaned_view._cleaned_text

    from core.paths import artifact_dir
    manifest = artifact_dir("unsaved", "recording") / "book.md.manifest.json"
    assert manifest.exists()
    assert manifest.read_text(encoding="utf-8").strip()

    window.close()


def test_book_job_failure_reports_the_error_without_crashing(
    monkeypatch, process_events,
):
    class _FailingBookPipeline(_FakeBookPipeline):
        def process(self, transcript_text, source_path, **kwargs):
            raise RuntimeError("LM Studio unreachable")

    monkeypatch.setattr("book_pipeline.BookPipeline", _FailingBookPipeline)
    from PyQt6.QtWidgets import QMessageBox
    from ui.main_window import MainWindow

    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args, **kwargs: warnings.append(args) or QMessageBox.StandardButton.Ok,
    )

    window = MainWindow()
    window._document_session.apply_result(_result())
    window._last_record_id = None
    window._source_filepath = None

    window.book_panel.run_single_requested.emit(True, False, "")
    runner = window._book_job
    assert runner is not None
    assert runner.wait(2000)
    process_events()

    assert window._book_job is None
    assert "LM Studio unreachable" in window.status_label.text()
    assert warnings

    window.close()


def test_cancel_book_job_stops_the_worker_without_reporting_a_result(
    monkeypatch, process_events,
):
    import threading

    release = threading.Event()
    started = threading.Event()

    class _BlockingBookPipeline(_FakeBookPipeline):
        def process(self, transcript_text, source_path, **kwargs):
            started.set()
            release.wait(5)
            is_cancelled = kwargs.get("is_cancelled")
            if is_cancelled is not None and is_cancelled():
                raise RuntimeError("cancelled")
            return super().process(transcript_text, source_path, **kwargs)

    monkeypatch.setattr("book_pipeline.BookPipeline", _BlockingBookPipeline)
    from ui.main_window import MainWindow

    window = MainWindow()
    window._document_session.apply_result(_result())
    window._last_record_id = None
    window._source_filepath = None

    window.book_panel.run_single_requested.emit(True, False, "")
    runner = window._book_job
    assert runner is not None
    assert started.wait(2)

    window._cancel_book_job()
    assert window._book_job is None
    release.set()
    assert runner.wait(2000)
    process_events()

    assert window.cleaned_view._cleaned_text == ""

    window.close()
