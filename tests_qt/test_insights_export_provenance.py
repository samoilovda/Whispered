"""InsightsPanel's "Save to file" writes an Artifact manifest for each
saved section (R5-full step 3 + the export feature added on top of it,
see docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md). Needs real Qt only because
InsightsPanel is a QWidget; _save_to_files() writes directly to
core.paths.output_dir() with no file dialog involved, so no hang risk
like the Cover/Article export flows.
"""

from __future__ import annotations


def _panel_with_results(record_id=None, source_path=None):
    from ui.insights_panel import InsightsPanel

    panel = InsightsPanel()
    if record_id is not None or source_path is not None:
        panel.set_provenance(record_id, source_path)
    panel.set_source_name("talk")
    panel._on_finished("chapters", [{"start": 0, "title": "Intro"}])
    panel._on_finished("action_items", [{"task": "Follow up", "owner": "Alice"}])
    return panel


def test_save_button_disabled_until_something_is_generated():
    from ui.insights_panel import InsightsPanel

    panel = InsightsPanel()
    assert panel._save_btn.isEnabled() is False

    panel._on_finished("chapters", [{"start": 0, "title": "Intro"}])
    assert panel._save_btn.isEnabled() is True

    panel.close()


def test_clear_disables_save_button_and_drops_results():
    panel = _panel_with_results()
    assert panel._save_btn.isEnabled() is True

    panel.clear()

    assert panel._save_btn.isEnabled() is False
    assert panel._results == {}

    panel.close()


def test_save_writes_a_txt_and_manifest_per_generated_section(tmp_path, monkeypatch):
    import ui.insights_panel as insights_panel_module
    from infrastructure.persistence import artifact_store

    monkeypatch.setattr(insights_panel_module, "output_dir", lambda: tmp_path)

    panel = _panel_with_results(record_id=7, source_path="/media/talk.mp4")
    panel._save_to_files()

    txt_files = sorted(p.name for p in tmp_path.glob("*.txt"))
    assert txt_files == ["talk_action_items.txt", "talk_chapters.txt"]
    assert "Intro" in (tmp_path / "talk_chapters.txt").read_text(encoding="utf-8")

    for name in txt_files:
        artifact = artifact_store.load(tmp_path / name)
        assert artifact is not None
        assert artifact.record_id == "7"
        assert artifact.source_path == "/media/talk.mp4"
        assert artifact.type.startswith("insights_")

    panel.close()


def test_save_without_provenance_uses_unsaved_sentinel(tmp_path, monkeypatch):
    import ui.insights_panel as insights_panel_module
    from infrastructure.persistence import artifact_store

    monkeypatch.setattr(insights_panel_module, "output_dir", lambda: tmp_path)

    panel = _panel_with_results()  # no set_provenance() call
    panel._save_to_files()

    artifact = artifact_store.load(tmp_path / "talk_chapters.txt")
    assert artifact is not None
    assert artifact.record_id == "unsaved"

    panel.close()


def test_save_with_nothing_generated_writes_no_files(tmp_path, monkeypatch):
    import ui.insights_panel as insights_panel_module

    monkeypatch.setattr(insights_panel_module, "output_dir", lambda: tmp_path)

    from ui.insights_panel import InsightsPanel
    panel = InsightsPanel()
    panel._save_to_files()  # nothing generated yet — must not raise or write

    assert list(tmp_path.glob("*.txt")) == []

    panel.close()


def test_manifest_write_failure_does_not_block_the_txt_files(tmp_path, monkeypatch):
    import ui.insights_panel as insights_panel_module
    import infrastructure.persistence.artifact_store as artifact_store_module

    monkeypatch.setattr(insights_panel_module, "output_dir", lambda: tmp_path)
    monkeypatch.setattr(
        artifact_store_module, "save",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )

    panel = _panel_with_results(record_id=1)
    panel._save_to_files()  # must not raise

    assert len(list(tmp_path.glob("*.txt"))) == 2

    panel.close()
