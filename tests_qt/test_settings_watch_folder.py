"""Real-Qt tests for the Settings > General watch-folder controls (see
docs/IMPROVEMENT_PLAN_2026-08.ru.md, B5b).
"""

from __future__ import annotations

import pytest


@pytest.fixture
def dialog(monkeypatch, tmp_path, process_events):
    import config
    from ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())

    dlg = SettingsDialog()
    yield dlg
    dlg.close()
    process_events()


def test_load_values_reflects_a_configured_watch_folder(dialog, monkeypatch, tmp_path):
    import config

    cfg = config.Config(watch_folder=str(tmp_path), watch_folder_enabled=True)
    monkeypatch.setattr(config, "_config", cfg)
    dialog._cfg = cfg
    dialog._load_values()

    assert dialog._watch_enabled_chk.isChecked()
    assert dialog._watch_folder_edit.text() == str(tmp_path)


def test_save_values_writes_the_watch_folder_back_to_config(dialog, tmp_path):
    dialog._watch_enabled_chk.setChecked(True)
    dialog._watch_folder_edit.setText(str(tmp_path))
    dialog._save_values()

    assert dialog._cfg.watch_folder_enabled is True
    assert dialog._cfg.watch_folder == str(tmp_path)


def test_save_values_strips_whitespace_from_the_folder_path(dialog, tmp_path):
    dialog._watch_folder_edit.setText(f"  {tmp_path}  ")
    dialog._save_values()

    assert dialog._cfg.watch_folder == str(tmp_path)


def test_disabled_by_default_on_a_fresh_config(dialog):
    assert not dialog._watch_enabled_chk.isChecked()
    assert dialog._watch_folder_edit.text() == ""
