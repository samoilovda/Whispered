"""Unit tests for application/export_controller.py — the Qt-free "what to
export and whether it worked" logic extracted out of
ui/main_window.py::_export_result (see
docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R6-cont).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from application.export_controller import (
    export_many_to_directory,
    export_preset,
    export_single,
    format_extension,
)
from domain.export_preset import BUILTIN_EXPORT_PRESETS_BY_KEY
from domain.transcription import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[Segment(start=0.0, end=1.0, text="hello world")],
        language="en",
        duration=1.0,
    )


def test_format_extension_shares_txt_between_txt_and_txt_ts():
    assert format_extension("txt") == "txt"
    assert format_extension("txt_ts") == "txt"
    assert format_extension("srt") == "srt"
    assert format_extension("json") == "json"


def test_export_single_writes_the_requested_format(tmp_path):
    filepath = str(tmp_path / "out.txt")
    export_single(_result(), filepath, "txt")
    assert "hello world" in open(filepath, encoding="utf-8").read()


def test_export_single_propagates_the_exception():
    """The single-file path shows the caller the actual error — must not
    swallow it, unlike the multi-format path."""
    with patch("application.export_controller.export_result", side_effect=ValueError("boom")):
        try:
            export_single(_result(), "/dev/null/impossible.txt", "txt")
        except ValueError as exc:
            assert str(exc) == "boom"
        else:
            raise AssertionError("expected ValueError to propagate")


def test_export_many_to_directory_writes_one_file_per_format(tmp_path):
    outcome = export_many_to_directory(
        _result(), str(tmp_path), ["txt", "srt", "json"], default_name="rec"
    )
    assert outcome.succeeded == ["txt", "srt", "json"]
    assert outcome.failed == []
    assert outcome.any_succeeded and not outcome.any_failed
    assert (tmp_path / "rec.txt").exists()
    assert (tmp_path / "rec.srt").exists()
    assert (tmp_path / "rec.json").exists()


def test_export_many_to_directory_txt_ts_gets_a_distinct_filename(tmp_path):
    outcome = export_many_to_directory(
        _result(), str(tmp_path), ["txt", "txt_ts"], default_name="rec"
    )
    assert outcome.succeeded == ["txt", "txt_ts"]
    assert (tmp_path / "rec.txt").exists()
    assert (tmp_path / "rec_ts.txt").exists()


def test_export_many_to_directory_one_failure_does_not_stop_the_rest(tmp_path):
    """A failure in one format must not prevent the others from being
    written — this is the whole point of reporting an ExportOutcome
    instead of raising on the first error."""
    real_export = __import__("application.export_controller", fromlist=["export_result"]).export_result

    def flaky(result, filepath, format_key):
        if format_key == "srt":
            raise RuntimeError("disk full")
        return real_export(result, filepath, format_key)

    with patch("application.export_controller.export_result", side_effect=flaky):
        outcome = export_many_to_directory(
            _result(), str(tmp_path), ["txt", "srt", "json"], default_name="rec"
        )

    assert outcome.succeeded == ["txt", "json"]
    assert outcome.failed == ["srt"]
    assert (tmp_path / "rec.txt").exists()
    assert (tmp_path / "rec.json").exists()
    assert not (tmp_path / "rec.srt").exists()


# ------------------------------------------------------------------ export_preset (B9)

@pytest.fixture(autouse=True)
def _isolated_artifact_dir(monkeypatch, tmp_path):
    """export_preset() locates materials via core.paths.artifact_dir(),
    which resolves under the real app output_dir() unless redirected —
    tests here must not touch real user data, and a fixed "source-<id>"
    dir name would collide across tests that reuse the same record id.
    Redirect it under this test's own tmp_path instead."""
    materials_root = tmp_path / "materials"

    def _dir(record_id, source):
        return materials_root / f"artifacts-{record_id}"

    monkeypatch.setattr("core.paths.artifact_dir", _dir)


def _write_material(step_name, result, source_path, record_id, content: bytes) -> None:
    """Write a generated material to disk exactly where
    application/steps.py's own step would (deterministic path via
    STEP_REGISTRY[step_name].make_artifact()), plus its provenance
    manifest — matching what a real recipe run leaves behind."""
    from application.steps import STEP_REGISTRY, StepContext
    from infrastructure.persistence import artifact_store
    from core.paths import artifact_dir

    context = StepContext(
        source_path=source_path, result=result, record_id=record_id,
        artifact_dir=artifact_dir(record_id, source_path),
    )
    artifact = STEP_REGISTRY[step_name].make_artifact(context)
    path = Path(artifact.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    artifact_store.save(artifact)


def test_export_preset_with_materials_present_collects_expected_files_and_writes_index(
    tmp_path,
):
    """B9 acceptance criterion: a record with a YouTube package and cover
    gets the expected set in the folder plus index.txt."""
    source_path = str(tmp_path / "source.mp3")
    Path(source_path).write_bytes(b"fake-audio")
    result = _result()
    record_id = 1
    _write_material("youtube_package", result, source_path, record_id, b'{"yt_titles": ["x"]}')
    _write_material("cover", result, source_path, record_id, b"PNGDATA")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    preset = BUILTIN_EXPORT_PRESETS_BY_KEY["youtube"]

    outcome = export_preset(
        result, preset, str(out_dir), record_id,
        source_path=source_path, default_name="source",
    )

    assert set(outcome.materials_copied) == {"youtube_package", "cover"}
    assert outcome.materials_missing == []
    assert outcome.formats.succeeded == ["srt", "vtt"]
    assert not outcome.any_missing
    assert outcome.total_files == 4
    assert (out_dir / "source.srt").exists()
    assert (out_dir / "source.vtt").exists()
    assert (out_dir / "youtube_package.json").exists()
    assert (out_dir / "cover.png").read_bytes() == b"PNGDATA"

    index_text = (out_dir / "index.txt").read_text(encoding="utf-8")
    assert "youtube_package.json" in index_text
    assert "cover.png" in index_text
    assert "srt" in index_text and "vtt" in index_text


def test_export_preset_without_materials_reports_them_missing_not_an_error(tmp_path):
    """B9 acceptance criterion: a record without those materials still
    gets what's there, and index.txt honestly says what's missing."""
    source_path = str(tmp_path / "source.mp3")
    result = _result()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    preset = BUILTIN_EXPORT_PRESETS_BY_KEY["youtube"]

    outcome = export_preset(
        result, preset, str(out_dir), record_id=1,
        source_path=source_path, default_name="source",
    )

    assert outcome.materials_copied == []
    assert set(outcome.materials_missing) == {"youtube_package", "cover"}
    assert outcome.formats.succeeded == ["srt", "vtt"]
    assert outcome.any_missing
    assert outcome.total_files == 2
    assert (out_dir / "source.srt").exists()
    assert not (out_dir / "youtube_package.json").exists()

    index_text = (out_dir / "index.txt").read_text(encoding="utf-8")
    assert "youtube_package: not generated for this record" in index_text
    assert "cover: not generated for this record" in index_text


def test_export_preset_article_draft_bundles_md_docx_and_article(tmp_path):
    source_path = str(tmp_path / "source.mp3")
    result = _result()
    record_id = 2
    _write_material(
        "article", result, source_path, record_id,
        b'{"blog_post": {"title": "T", "content": "C"}}',
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    preset = BUILTIN_EXPORT_PRESETS_BY_KEY["article_draft"]

    outcome = export_preset(
        result, preset, str(out_dir), record_id,
        source_path=source_path, default_name="source",
    )

    assert outcome.formats.succeeded == ["md", "docx"]
    assert outcome.materials_copied == ["article"]
    assert (out_dir / "articles.json").exists()


def test_export_preset_archive_includes_every_present_material(tmp_path):
    source_path = str(tmp_path / "source.mp3")
    result = _result()
    record_id = 3
    _write_material("article", result, source_path, record_id, b"{}")
    _write_material("book", result, source_path, record_id, b"# Book")
    # insights and cover/youtube_package deliberately left ungenerated.
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    preset = BUILTIN_EXPORT_PRESETS_BY_KEY["archive"]

    outcome = export_preset(
        result, preset, str(out_dir), record_id,
        source_path=source_path, default_name="source",
    )

    assert set(outcome.materials_copied) == {"article", "book"}
    assert set(outcome.materials_missing) == {"insights", "youtube_package", "cover"}
    # PDF requires a Qt display unavailable under headless/system-python
    # test runs (see tests/test_exporters.py's own TestExportResult) —
    # every other format still succeeds.
    assert set(outcome.formats.succeeded) == {
        "txt", "txt_ts", "srt", "vtt", "json", "md", "html", "docx",
    }


def test_export_preset_writes_index_even_when_directory_has_no_materials(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    preset = BUILTIN_EXPORT_PRESETS_BY_KEY["article_draft"]

    outcome = export_preset(
        _result(), preset, str(out_dir), record_id=1,
        source_path="", default_name="rec",
    )

    assert outcome.index_path == str(out_dir / "index.txt")
    assert (out_dir / "index.txt").exists()


def test_export_preset_a_copy_failure_is_reported_not_raised(tmp_path, monkeypatch):
    source_path = str(tmp_path / "source.mp3")
    result = _result()
    record_id = 4
    _write_material("article", result, source_path, record_id, b"{}")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    preset = BUILTIN_EXPORT_PRESETS_BY_KEY["article_draft"]

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("application.export_controller.shutil.copyfile", _boom)

    outcome = export_preset(
        result, preset, str(out_dir), record_id,
        source_path=source_path, default_name="source",
    )

    assert outcome.materials_copied == []
    assert outcome.materials_missing == ["article"]
    assert "copy failed" in (out_dir / "index.txt").read_text(encoding="utf-8")


def test_export_preset_total_files_counts_formats_and_materials_together(tmp_path):
    source_path = str(tmp_path / "source.mp3")
    result = _result()
    record_id = 5
    _write_material("article", result, source_path, record_id, b"{}")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    preset = BUILTIN_EXPORT_PRESETS_BY_KEY["article_draft"]

    outcome = export_preset(
        result, preset, str(out_dir), record_id,
        source_path=source_path, default_name="source",
    )

    assert outcome.total_files == len(outcome.formats.succeeded) + len(outcome.materials_copied)
    assert outcome.total_files == 3
