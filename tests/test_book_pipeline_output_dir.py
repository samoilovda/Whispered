"""Tests for book_pipeline.py output_dir parameter (R12).

No LM Studio required — LMStudioClient and _call_lm are mocked.
"""

from __future__ import annotations

import book_pipeline

import pytest


class _FakeClient:
    """Minimal stand-in for LMStudioClient."""
    def __init__(self, base_url=""):
        self.base_url = base_url
        self.is_cancelled = None


@pytest.fixture(autouse=True)
def _patch_lm_client(monkeypatch):
    monkeypatch.setattr(book_pipeline, "LMStudioClient", _FakeClient)


def _make_pipeline(monkeypatch):
    """Return a BookPipeline with _call_lm mocked to return processed text."""

    def fake_call_lm(client, text, system_prompt, temperature, is_cancelled, on_progress):
        return "processed text"

    monkeypatch.setattr(book_pipeline, "_call_lm", fake_call_lm)
    return book_pipeline.BookPipeline()


class TestBookPipelineOutputDir:
    def test_output_goes_to_explicit_output_dir(self, tmp_path, monkeypatch):
        """When output_dir is given, files must be written there, not to source.parent."""
        source = tmp_path / "sources" / "talk.md"
        source.parent.mkdir()
        source.write_text("hello world", encoding="utf-8")

        out_dir = tmp_path / "outputs"

        pipeline = _make_pipeline(monkeypatch)
        result = pipeline.process(
            transcript_text="hello world",
            source_path=str(source),
            output_dir=str(out_dir),
            do_unwrap=True,
        )

        assert result.success, f"Pipeline failed: {result.stages}"

        out_files = list(out_dir.iterdir())
        assert len(out_files) == 1, f"Expected 1 output file, got {out_files}"
        assert out_files[0].suffix == ".md"

        source_parent_files = [f for f in source.parent.iterdir() if f != source]
        assert source_parent_files == [], (
            f"Output written to source.parent: {source_parent_files}"
        )

    def test_output_dir_is_created_if_missing(self, tmp_path, monkeypatch):
        source = tmp_path / "talk.md"
        source.write_text("text", encoding="utf-8")

        out_dir = tmp_path / "deep" / "output" / "dir"
        assert not out_dir.exists()

        pipeline = _make_pipeline(monkeypatch)
        result = pipeline.process(
            transcript_text="text",
            source_path=str(source),
            output_dir=str(out_dir),
            do_unwrap=True,
        )

        assert result.success
        assert out_dir.exists()

    def test_defaults_to_source_parent_when_no_output_dir(self, tmp_path, monkeypatch):
        """Backwards-compatibility: without output_dir, files go to source.parent."""
        source = tmp_path / "talk.md"
        source.write_text("text", encoding="utf-8")

        pipeline = _make_pipeline(monkeypatch)
        result = pipeline.process(
            transcript_text="text",
            source_path=str(source),
            do_unwrap=True,
        )

        assert result.success
        files = [f for f in tmp_path.iterdir() if f != source]
        assert len(files) == 1
