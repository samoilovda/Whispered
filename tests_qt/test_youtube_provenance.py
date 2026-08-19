"""YouTubePanel writes an Artifact manifest for each saved file (R5-full
step 3, see docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md). Needs real Qt only
because YouTubePanel is a QWidget; neither save_all() nor _save_to_file()
drives a file dialog, so no hang risk like the Cover/Article export flows.
"""

from __future__ import annotations


def _make_panel():
    from ui.youtube_panel import YouTubePanel

    panel = YouTubePanel()
    panel._chapters_edit.setPlainText("00:00 Intro")
    panel._titles_edit.setPlainText("A Great Title")
    return panel


def test_save_all_writes_a_manifest_per_saved_file(tmp_path):
    from infrastructure.persistence import artifact_store

    panel = _make_panel()
    panel.set_provenance(record_id=7, source_path="/media/talk.mp4")
    panel.set_segments([{"start": 0.0, "end": 1.0, "text": "hello"}], transcript_language="en")
    panel.set_source_name("talk")

    saved = panel.save_all(tmp_path)

    assert len(saved) == 2  # only the two tabs with text above
    for path in saved:
        artifact = artifact_store.load(path)
        assert artifact is not None
        assert artifact.record_id == "7"
        assert artifact.source_path == "/media/talk.mp4"
        assert artifact.type.startswith("youtube_")

    panel.close()


def test_save_all_without_provenance_uses_unsaved_sentinel(tmp_path):
    from infrastructure.persistence import artifact_store

    panel = _make_panel()
    # No set_provenance() call — matches generating YouTube content before
    # the transcript was ever saved to history.
    saved = panel.save_all(tmp_path)

    artifact = artifact_store.load(saved[0])
    assert artifact is not None
    assert artifact.record_id == "unsaved"

    panel.close()


def test_manifest_write_failure_does_not_block_the_saved_file(tmp_path, monkeypatch):
    import infrastructure.persistence.artifact_store as artifact_store_module

    panel = _make_panel()
    panel.set_provenance(record_id=1, source_path=None)

    monkeypatch.setattr(
        artifact_store_module, "save",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )

    # Must not raise even though the manifest write fails internally.
    saved = panel.save_all(tmp_path)
    assert len(saved) == 2
    assert all(p.exists() for p in saved)

    panel.close()


def test_save_to_file_current_tab_also_writes_a_manifest(tmp_path, monkeypatch):
    from infrastructure.persistence import artifact_store
    import ui.youtube_panel as youtube_panel_module

    monkeypatch.setattr(youtube_panel_module, "_OUTPUT_DIR", tmp_path)

    panel = _make_panel()
    panel.set_provenance(record_id=3, source_path=None)
    panel.set_source_name("talk")
    panel._tabs.setCurrentIndex(0)  # "chapters" tab, has text from _make_panel

    panel._save_to_file()

    files = list(tmp_path.glob("*.txt"))
    assert len(files) == 1
    artifact = artifact_store.load(files[0])
    assert artifact is not None
    assert artifact.record_id == "3"

    panel.close()
