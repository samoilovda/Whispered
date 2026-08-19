"""AIProcessingWorker._run_book_unwrap() forwards provenance kwargs to
BookPipeline.process() (R5-full step 3, see
docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md). Qt is stubbed via
tests/conftest.py.
"""

from __future__ import annotations

import sys

# tests/conftest.py eagerly stubs core.ai_worker as a bare `object` (guarded
# by "if not already in sys.modules") so other test files' unrelated imports
# succeed without pulling in the real module's heavy dependency chain. This
# file specifically tests the real class, so it must be popped first — same
# pattern as tests/test_insights_worker_provider.py's core.lm_client reset.
sys.modules.pop("core.ai_worker", None)

from core.ai_worker import AIProcessingWorker  # noqa: E402


def test_book_unwrap_forwards_provenance_kwargs_to_pipeline_process(monkeypatch):
    captured = {}

    class _FakePipeline:
        def __init__(self):
            self._client = type("C", (), {"is_cancelled": None})()

        def process(self, **kwargs):
            captured.update(kwargs)
            return "book-result"

    import book_pipeline
    monkeypatch.setattr(book_pipeline, "BookPipeline", _FakePipeline)

    worker = AIProcessingWorker(
        "book_unwrap", "transcript text",
        source_path="/media/talk.mp4",
        record_id=42,
        source_hash="abc123",
        transcript_revision="rev-1",
    )
    results = []
    worker.finished.connect(lambda r: results.append(r))
    worker._execute()

    assert results == ["book-result"]
    assert captured["record_id"] == 42
    assert captured["source_hash"] == "abc123"
    assert captured["transcript_revision"] == "rev-1"


def test_book_unwrap_without_provenance_kwargs_forwards_none(monkeypatch):
    """A caller that doesn't pass provenance (e.g. an older call site)
    must not crash — process() treats None/None as "no manifest"."""
    captured = {}

    class _FakePipeline:
        def __init__(self):
            self._client = type("C", (), {"is_cancelled": None})()

        def process(self, **kwargs):
            captured.update(kwargs)
            return "book-result"

    import book_pipeline
    monkeypatch.setattr(book_pipeline, "BookPipeline", _FakePipeline)

    worker = AIProcessingWorker("book_unwrap", "transcript text", source_path="/media/talk.mp4")
    worker._execute()

    assert captured["record_id"] is None
    assert captured["source_hash"] is None
    assert captured["transcript_revision"] is None
