"""application.export_plan — review the bundle, then write exactly it
(UI/UX audit 2026-10-18, UX-15 / T15)."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from application.export_plan import (
    CURRENT, MISSING, STALE, UNKNOWN, PlanOutdated, build_export_plan,
    execute_export_plan, find_item,
)
from domain.export_preset import ExportPreset
from domain.transcription import Segment, TranscriptionResult


def _result(text="hello world"):
    return TranscriptionResult(segments=[Segment(0.0, 1.0, text)], language="en", duration=1.0)


@pytest.fixture(autouse=True)
def _artifact_root(monkeypatch, tmp_path):
    root = tmp_path / "materials"
    monkeypatch.setattr("core.paths.artifact_dir", lambda record_id, source: root / f"a-{record_id}")
    return root


def _material(step, result, record_id, content=b"{}", revision_of=None):
    from application.steps import STEP_REGISTRY, StepContext
    from core.paths import artifact_dir
    from infrastructure.persistence import artifact_store

    context = StepContext(source_path="/m/src.mp3", result=revision_of or result,
                          record_id=record_id, artifact_dir=artifact_dir(record_id, "/m/src.mp3"))
    artifact = STEP_REGISTRY[step].make_artifact(context)
    path = Path(artifact.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    artifact_store.save(artifact)
    return path


PRESET = ExportPreset("t", formats=("srt", "txt"), artifacts=("article", "book", "youtube_package"))


def test_plan_reports_freshness_missing_and_conflicts(tmp_path):
    result = _result()
    _material("article", result, 1)                                # current
    _material("book", result, 1, b"# b", revision_of=_result("older text"))  # stale
    out = tmp_path / "out"
    out.mkdir()
    (out / "src.srt").write_text("existing")
    plan = build_export_plan(result, PRESET, str(out), 1, media_source="/m/src.mp3",
                             default_name="src")
    assert find_item(plan, "article").freshness == CURRENT
    assert find_item(plan, "book").freshness == STALE
    assert find_item(plan, "youtube_package").freshness == MISSING
    assert [i.target for i in plan.conflicts] == ["src.srt"]


def test_execute_writes_exactly_the_plan_and_keeps_existing_files(tmp_path):
    result = _result()
    _material("article", result, 1)
    out = tmp_path / "out"
    out.mkdir()
    (out / "src.srt").write_text("existing")
    plan = build_export_plan(result, PRESET, str(out), 1, default_name="src")
    report = execute_export_plan(plan, result, overwrite=False)
    assert (out / "src.srt").read_text() == "existing"
    assert set(report.written) == {"src.txt", "articles.json"}
    index = (out / "index.txt").read_text(encoding="utf-8")
    assert "kept the existing file" in index and "articles.json [current]" in index
    assert "youtube_package: not generated for this record" in index


def test_changed_transcript_after_review_is_refused(tmp_path):
    result = _result()
    plan = build_export_plan(result, PRESET, str(tmp_path), 1, default_name="src")
    result.segments[0].text = "edited after review"
    with pytest.raises(PlanOutdated):
        execute_export_plan(plan, result, overwrite=True)


def test_user_edits_travel_beside_the_material_not_into_it(tmp_path):
    from application.user_edits import overlay_path, save_overlay

    result = _result()
    package = _material("youtube_package", result, 1, b'{"yt_titles": ["model"]}')
    save_overlay(overlay_path(package), {"title": "Mine", "title_base": "x"})
    out = tmp_path / "out"
    out.mkdir()
    plan = build_export_plan(result, PRESET, str(out), 1, default_name="src")
    item = find_item(plan, "youtube_package")
    assert item.user_edits == ("title",)
    execute_export_plan(plan, result, overwrite=True)
    assert (out / package.name).read_bytes() == b'{"yt_titles": ["model"]}'
    assert (out / overlay_path(package).name).exists()
    assert "your edits: title" in (out / "index.txt").read_text(encoding="utf-8")


def test_one_failing_format_does_not_stop_the_rest(tmp_path, monkeypatch):
    import exporters

    real = exporters.export_result

    def flaky(result, path, key):
        if key == "srt":
            raise OSError("disk full")
        return real(result, path, key)

    monkeypatch.setattr(exporters, "export_result", flaky)
    result = _result()
    plan = build_export_plan(result, PRESET, str(tmp_path), 1, default_name="src")
    report = execute_export_plan(plan, result, overwrite=True)
    assert report.failed == [("src.srt", "disk full")]
    assert "src.txt" in report.written


def test_material_already_in_the_target_folder_is_not_copied_onto_itself(tmp_path):
    result = _result()
    path = _material("article", result, 1)
    plan = build_export_plan(result, PRESET, str(path.parent), 1, default_name="src")
    item = find_item(plan, "article")
    assert item.same_file and not item.will_write
    report = execute_export_plan(plan, result, overwrite=True)
    assert ("article", "same_file") in report.skipped
    assert path.read_bytes() == b"{}"


def test_manifest_without_revision_is_unknown(tmp_path):
    from infrastructure.persistence import artifact_store

    result = _result()
    path = _material("article", result, 1)
    saved = artifact_store.load(path)
    artifact_store.save(dataclasses.replace(saved, transcript_revision=""))
    plan = build_export_plan(result, PRESET, str(tmp_path / "x"), 1, default_name="src")
    assert find_item(plan, "article").freshness == UNKNOWN


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX non-root only")
def test_read_only_folder_reports_failures(tmp_path):
    result = _result()
    out = tmp_path / "ro"
    out.mkdir()
    out.chmod(0o500)
    try:
        plan = build_export_plan(result, PRESET, str(out), 1, default_name="src")
        report = execute_export_plan(plan, result, overwrite=True)
    finally:
        out.chmod(0o700)
    assert not report.written and len(report.failed) == 3       # two files and the index
    assert report.index_path == ""


def test_existing_index_and_overlay_are_protected_unless_overwriting(tmp_path):
    from application.user_edits import overlay_path, save_overlay

    result = _result()
    package = _material("youtube_package", result, 1, b'{"yt_titles": ["model"]}')
    save_overlay(overlay_path(package), {"title": "Mine", "title_base": "x"})
    out = tmp_path / "out"
    out.mkdir()
    (out / "index.txt").write_text("KEEP ME")
    (out / overlay_path(package).name).write_text("OLD EDITS")
    plan = build_export_plan(result, PRESET, str(out), 1, default_name="src")
    assert {"index.txt", package.name} <= {i.target for i in plan.conflicts}
    report = execute_export_plan(plan, result, overwrite=False)
    assert (out / "index.txt").read_text() == "KEEP ME"
    assert (out / overlay_path(package).name).read_text() == "OLD EDITS"
    assert report.index_path == str(out / "index (2).txt") and (out / "index (2).txt").exists()
    execute_export_plan(plan, result, overwrite=True)
    assert (out / "index.txt").read_text() != "KEEP ME"
    assert (out / overlay_path(package).name).read_text() != "OLD EDITS"
