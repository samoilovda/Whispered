"""Real-Qt test for MainWindow's "Articles" button (RecordView.articles_btn
-> articles_requested -> _start_generate_all), now routed through
application/steps.py's "article" step via JobRunner instead of the generic
AIProcessingWorker (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B5b — the
same migration B5a did for clean).
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


class _FakeArticleGenerator:
    """Matches tests/test_steps.py's fake — same shape _article_runner
    needs (``lm_client.is_cancelled`` must be settable)."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.lm_client = _FakeLMClient()

    def generate_all_formats(self, text, formats=None, on_progress=None):
        from article_generator import Article, ArticleFormat, GenerationResult, TopicAnalysis

        if on_progress is not None:
            on_progress(50, "generating...")
        articles = [
            Article(title=f"Title {fmt.value}", format=fmt, content=f"Body {fmt.value}")
            for fmt in (formats or list(ArticleFormat))
        ]
        return GenerationResult(
            source_text=text, topic_analysis=TopicAnalysis(), articles=articles,
        )


def test_articles_button_runs_through_job_runner_and_writes_provenance(
    monkeypatch, process_events,
):
    monkeypatch.setattr("article_generator.ArticleGenerator", _FakeArticleGenerator)
    from ui.main_window import MainWindow

    window = MainWindow()
    window._document_session.apply_result(_result())
    window._last_record_id = None
    window._source_filepath = None

    window._start_generate_all()
    runner = window._article_job
    assert runner is not None

    assert runner.wait(2000)
    process_events()

    assert window._article_job is None
    assert window.article_view.has_articles()

    from core.paths import artifact_dir
    manifest = artifact_dir("unsaved", "recording") / "articles.json.manifest.json"
    assert manifest.exists()
    assert manifest.read_text(encoding="utf-8").strip()

    window.close()


def test_articles_use_the_cleaned_text_when_available(monkeypatch, process_events):
    seen = {}

    class _RecordingArticleGenerator(_FakeArticleGenerator):
        def generate_all_formats(self, text, formats=None, on_progress=None):
            seen["text"] = text
            return super().generate_all_formats(text, formats=formats, on_progress=on_progress)

    monkeypatch.setattr("article_generator.ArticleGenerator", _RecordingArticleGenerator)
    from ui.main_window import MainWindow

    window = MainWindow()
    window._document_session.apply_result(_result())
    window._last_record_id = None
    window._source_filepath = None
    window._cleaned_text = "Already cleaned text."

    window._start_generate_all()
    runner = window._article_job
    assert runner is not None
    assert runner.wait(2000)
    process_events()

    assert seen["text"] == "Already cleaned text."

    window.close()


def test_article_job_failure_reports_the_error_without_crashing(
    monkeypatch, process_events,
):
    class _FailingArticleGenerator(_FakeArticleGenerator):
        def generate_all_formats(self, text, formats=None, on_progress=None):
            raise RuntimeError("LM Studio unreachable")

    monkeypatch.setattr("article_generator.ArticleGenerator", _FailingArticleGenerator)
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

    window._start_generate_all()
    runner = window._article_job
    assert runner is not None
    assert runner.wait(2000)
    process_events()

    assert window._article_job is None
    assert not window.article_view.has_articles()
    assert "LM Studio unreachable" in window.status_label.text()
    assert warnings

    window.close()


def test_cancel_article_job_stops_the_worker_without_reporting_a_result(
    monkeypatch, process_events,
):
    import threading

    release = threading.Event()
    started = threading.Event()

    class _BlockingArticleGenerator(_FakeArticleGenerator):
        def generate_all_formats(self, text, formats=None, on_progress=None):
            started.set()
            release.wait(5)
            if self.lm_client.is_cancelled():
                raise RuntimeError("cancelled")
            return super().generate_all_formats(text, formats=formats, on_progress=on_progress)

    monkeypatch.setattr("article_generator.ArticleGenerator", _BlockingArticleGenerator)
    from ui.main_window import MainWindow

    window = MainWindow()
    window._document_session.apply_result(_result())
    window._last_record_id = None
    window._source_filepath = None

    window._start_generate_all()
    runner = window._article_job
    assert runner is not None
    assert started.wait(2)

    window._cancel_article_job()
    assert window._article_job is None
    release.set()
    assert runner.wait(2000)
    process_events()

    assert not window.article_view.has_articles()

    window.close()
