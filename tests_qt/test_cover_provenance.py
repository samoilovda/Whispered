"""CoverView writes an Artifact manifest on export (R5-full step 3, see
docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md). Needs real Qt: CoverView renders
a real QImage in __init__/set_segments.

Exercises CoverView._write_provenance() directly against real exported
files rather than driving the full _export() flow through QFileDialog —
this is the actual new logic under test, and avoids depending on how a
native file dialog behaves under the offscreen QPA platform.
"""

from __future__ import annotations

from pathlib import Path


def _make_cover_view():
    from ui.cover_view import CoverView

    view = CoverView()
    view.render_preview()
    return view


def _export_real_files(view, tmp_path: Path) -> list[Path]:
    from covers.export import export

    return export(view.last_image, None, tmp_path, "test-cover", state={})


def test_export_writes_an_artifact_manifest_next_to_the_png(tmp_path):
    from infrastructure.persistence import artifact_store

    view = _make_cover_view()
    view.set_provenance(record_id=42, source_path=None)
    view.set_segments(
        [{"start": 0.0, "end": 1.0, "text": "hello world"}], transcript_language="en"
    )

    files = _export_real_files(view, tmp_path)
    view._write_provenance(files)

    png = next(f for f in files if f.suffix == ".png")
    artifact = artifact_store.load(png)
    assert artifact is not None
    assert artifact.record_id == "42"
    assert artifact.type == "cover"
    assert artifact.path == str(png)

    view.close()


def test_export_without_a_record_id_uses_unsaved_sentinel(tmp_path):
    from infrastructure.persistence import artifact_store

    view = _make_cover_view()
    # No set_provenance() call — matches a cover generated before the
    # transcript was ever saved to history.
    view.set_segments([{"start": 0.0, "end": 1.0, "text": "hi"}], transcript_language="en")

    files = _export_real_files(view, tmp_path)
    view._write_provenance(files)

    png = next(f for f in files if f.suffix == ".png")
    artifact = artifact_store.load(png)
    assert artifact is not None
    assert artifact.record_id == "unsaved"

    view.close()


def test_manifest_write_failure_does_not_raise(tmp_path, monkeypatch):
    """The PNG/JPEG are already safely on disk by the time the manifest is
    written — a manifest failure must not turn a successful export into a
    reported error."""
    from infrastructure.persistence import artifact_store

    view = _make_cover_view()
    view.set_provenance(record_id=1, source_path=None)

    files = _export_real_files(view, tmp_path)

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(artifact_store, "save", _boom)

    # Must not raise.
    view._write_provenance(files)

    view.close()


def test_manifest_provider_and_prompt_version_are_not_empty(tmp_path):
    """B5f (see docs/UI_REDESIGN_PLAN_2026-09.ru.md): matches
    application/steps.py's "cover" step artifact, which B0 already fixed —
    this export path shares the same fields, not the step itself, since
    cover's render has no worker to route through JobRunner."""
    from infrastructure.persistence import artifact_store

    view = _make_cover_view()
    view.set_provenance(record_id=1, source_path=None)

    files = _export_real_files(view, tmp_path)
    view._write_provenance(files)

    png = next(f for f in files if f.suffix == ".png")
    artifact = artifact_store.load(png)
    assert artifact.provider
    assert artifact.prompt_version

    view.close()


def test_different_transcripts_get_different_revisions_in_the_manifest(tmp_path):
    from infrastructure.persistence import artifact_store

    view = _make_cover_view()
    view.set_provenance(record_id=1, source_path=None)
    view.set_segments([{"start": 0.0, "end": 1.0, "text": "first version"}], transcript_language="en")
    files_a = _export_real_files(view, tmp_path / "a")
    view._write_provenance(files_a)

    view.set_segments([{"start": 0.0, "end": 1.0, "text": "edited version"}], transcript_language="en")
    files_b = _export_real_files(view, tmp_path / "b")
    view._write_provenance(files_b)

    artifact_a = artifact_store.load(next(f for f in files_a if f.suffix == ".png"))
    artifact_b = artifact_store.load(next(f for f in files_b if f.suffix == ".png"))
    assert artifact_a.transcript_revision != artifact_b.transcript_revision

    view.close()
