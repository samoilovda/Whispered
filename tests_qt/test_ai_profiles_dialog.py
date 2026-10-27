"""The profiles and templates dialog (N05)."""

from __future__ import annotations

import json

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

import config
from core import prompt_templates
from core.i18n import load_locale, tr
from domain import ai_profile as m


@pytest.fixture
def dialog(monkeypatch, tmp_path, process_events):
    from ui.ai_profiles_dialog import AiProfilesDialog

    load_locale("en")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    monkeypatch.setattr("core.paths.data_dir", lambda: tmp_path)
    dlg = AiProfilesDialog()
    dlg.show()
    yield dlg, tmp_path
    dlg.close()


def test_default_is_listed_first_and_not_editable(dialog):
    dlg, _ = dialog
    assert dlg.profile_list.item(0).text().startswith(m.DEFAULT_PROFILE)
    assert not dlg.name_edit.isEnabled() and not dlg.save_btn.isEnabled()


def test_new_edit_use_and_delete_a_profile(dialog):
    dlg, _ = dialog
    dlg._new_profile()
    assert dlg.name_edit.isEnabled() and dlg.name_edit.text() == "Profile"
    dlg.endpoint_edit.setText("http://localhost:5678/v1")
    dlg.model_edit.setText("gemma-big")
    dlg.step_checks["article"].setChecked(True)
    dlg._save_profile()
    cfg = config.get_config()
    assert cfg.lm_profiles[0]["model"] == "gemma-big" and cfg.lm_profiles[0]["steps"] == ["article"]
    dlg._use_profile()
    assert cfg.lm_profile_active == "Profile"
    assert "in use" in dlg.profile_list.currentItem().text()
    dlg._delete_profile()
    assert cfg.lm_profiles == [] and cfg.lm_profile_active == ""


def test_invalid_input_is_explained_and_nothing_is_saved(dialog):
    dlg, _ = dialog
    dlg._new_profile()
    dlg.endpoint_edit.setText("not a url")
    dlg._save_profile()
    assert "http" in dlg.profile_error.text()
    assert config.get_config().lm_profiles[0]["endpoint"] != "not a url"
    dlg.endpoint_edit.setText("https://example.com/v1")
    dlg._save_profile()
    dlg._show_profile(dlg.profile_list.currentRow())
    assert dlg.profile_note.text() == tr("ai_profile_remote_warning")   # transcript would leave this computer


def test_import_rejects_a_credential_and_accepts_a_clean_profile(dialog, monkeypatch, tmp_path):
    dlg, _ = dialog
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "X", "endpoint": "http://localhost:1/v1", "api_key": "sk-1"}))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "")))
    dlg._import_profile()
    assert warnings and "credentials" in warnings[0] and config.get_config().lm_profiles == []
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"name": "Imported", "endpoint": "http://localhost:9/v1"}))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(good), "")))
    dlg._import_profile()
    assert [p["name"] for p in config.get_config().lm_profiles] == ["Imported"]
    assert config.get_config().lm_profile_active == ""     # imported, never activated


def test_template_copy_edit_use_and_remove(dialog):
    dlg, _ = dialog
    dlg.base_combo.setCurrentText("cleaning")
    dlg._copy_builtin()
    assert dlg.template_list.count() == 1 and dlg.template_text.isEnabled()
    dlg.template_text.setPlainText("My cleaning rules.")
    dlg.example_in.setPlainText("uh hello")
    dlg.example_out.setPlainText("Hello.")
    dlg.use_check.setChecked(True)
    dlg._save_template()
    cfg = config.get_config()
    assert cfg.prompt_template_active == {"cleaning": "My cleaning"}
    saved = prompt_templates.load("My cleaning")
    assert saved.version == 2 and saved.examples[0].output == "Hello."
    from core.prompts import load_prompt
    assert load_prompt("cleaning").startswith("My cleaning rules.")
    dlg.template_text.setPlainText("   ")
    dlg._save_template()
    assert "empty" in dlg.template_error.text()
    dlg._delete_template()
    assert cfg.prompt_template_active == {} and prompt_templates.list_templates() == []


def test_connection_probe_is_owned_and_single(dialog, monkeypatch, process_events):
    import threading

    from core.lm_status_worker import LMStatusWorker

    dlg, _ = dialog
    release = threading.Event()
    started = []

    def slow(self):
        started.append(1)
        release.wait(5)
        self.status_ready.emit(True, "late-model")

    monkeypatch.setattr(LMStatusWorker, "_execute", slow)
    dlg.endpoint_edit.setText("http://localhost:1/v1")
    dlg._test_profile()
    dlg._test_profile()                                # a second click while it runs
    assert not dlg.test_btn.isEnabled()
    assert dlg._registry.active_names == ["lm_status_checker"]
    dlg.done(0)                                        # closed mid-probe: retired, not waited for
    shown = dlg.test_result.text()
    release.set()
    assert dlg._registry.shutdown_all(5000) == []
    process_events()
    assert len(started) == 1 and dlg.test_result.text() == shown


def test_a_probe_result_for_an_edited_endpoint_is_discarded(dialog):
    dlg, _ = dialog
    dlg.endpoint_edit.setText("http://other/v1")
    dlg._on_tested(True, "model", "http://localhost:1/v1")
    assert dlg.test_result.text() == "" and dlg.test_btn.isEnabled()
