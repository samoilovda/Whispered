"""Unit tests for core/book_batch_worker.py's Artifact provenance wiring
(R5-full step 3, see docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md). Qt is
stubbed via tests/conftest.py; _execute() is called directly rather than
through .start(), matching the pattern used for other *_worker.py tests.
"""

from __future__ import annotations

import book_pipeline
from core.book_batch_worker import BookBatchWorker


class _FakeClient:
    def __init__(self, base_url=""):
        self.base_url = base_url
        self.is_cancelled = None


def _stub_pipeline(monkeypatch):
    monkeypatch.setattr(book_pipeline, "LMStudioClient", _FakeClient)
    monkeypatch.setattr(
        book_pipeline, "_call_lm",
        lambda client, text, system_prompt, temperature, is_cancelled, on_progress: "processed",
    )


def test_batch_item_gets_unsaved_sentinel_and_a_content_revision(tmp_path, monkeypatch):
    from infrastructure.persistence import artifact_store

    _stub_pipeline(monkeypatch)
    source = tmp_path / "talk.md"
    source.write_text("original transcript text", encoding="utf-8")

    worker = BookBatchWorker([str(source)], do_unwrap=True)
    results = []
    worker.file_finished.connect(lambda i, r: results.append(r))
    worker._execute()

    assert len(results) == 1
    result = results[0]
    assert result.success
    artifact = artifact_store.load(result.stages[0].output_path)
    assert artifact is not None
    assert artifact.record_id == "unsaved"
    assert artifact.transcript_revision  # non-empty, derived from file content


def test_different_file_content_gives_a_different_revision(tmp_path, monkeypatch):
    from infrastructure.persistence import artifact_store

    _stub_pipeline(monkeypatch)
    a = tmp_path / "a.md"
    a.write_text("first transcript", encoding="utf-8")
    b = tmp_path / "b.md"
    b.write_text("second, completely different transcript", encoding="utf-8")

    worker = BookBatchWorker([str(a), str(b)], do_unwrap=True)
    results = []
    worker.file_finished.connect(lambda i, r: results.append(r))
    worker._execute()

    assert len(results) == 2
    artifact_a = artifact_store.load(results[0].stages[0].output_path)
    artifact_b = artifact_store.load(results[1].stages[0].output_path)
    assert artifact_a.transcript_revision != artifact_b.transcript_revision
