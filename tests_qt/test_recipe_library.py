"""Real-Qt tests for B4 (docs/IMPROVEMENT_PLAN_2026-08.ru.md): named,
saved custom recipes with their own transcription params — start_view's
chip row, and _start_transcription reading a recipe's params override.

Save/Cancel-through-Config coverage for the recipe editor already lives
in tests_qt/test_ui_audit_regressions.py (from the earlier A4 work this
extends); this file covers what's new: multiple named recipes, Save as
new, Delete, and params actually reaching Transcriber.transcribe().
"""

from __future__ import annotations

import pytest


@pytest.fixture
def window(monkeypatch, tmp_path, process_events):
    import config
    from ui.main_window import MainWindow

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())

    win = MainWindow()
    yield win
    win.close()
    process_events()


# ------------------------------------------------------------------ start_view chips

def test_start_view_grows_a_chip_for_every_saved_custom_recipe(
    monkeypatch, tmp_path, process_events,
):
    import config
    from ui.main_window import MainWindow

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config(recipes=[
        {"name": "Quick notes", "steps": ["transcribe", "clean"], "builtin_key": ""},
        {"name": "Deep dive", "steps": ["transcribe", "diarize", "insights"], "builtin_key": ""},
    ]))

    window = MainWindow()
    process_events()

    assert "Quick notes" in window.start_view._recipe_buttons
    assert "Deep dive" in window.start_view._recipe_buttons
    assert window.start_view._recipe_buttons["Quick notes"].text() == "Quick notes"

    window.close()
    process_events()


def test_refresh_recipe_chips_reflects_config_changes(window, process_events):
    import config

    assert "Custom A" not in window.start_view._recipe_buttons

    cfg = config.get_config()
    cfg.recipes = [{"name": "Custom A", "steps": ["transcribe"], "builtin_key": ""}]
    window.start_view.refresh_recipe_chips()
    process_events()

    assert "Custom A" in window.start_view._recipe_buttons

    cfg.recipes = []
    window.start_view.refresh_recipe_chips()
    process_events()

    assert "Custom A" not in window.start_view._recipe_buttons


def test_selecting_a_custom_recipe_chip_persists_and_checks_it(window, process_events):
    import config

    cfg = config.get_config()
    cfg.recipes = [{"name": "Custom A", "steps": ["transcribe"], "builtin_key": ""}]
    window.start_view.refresh_recipe_chips()
    process_events()

    window.start_view._recipe_buttons["Custom A"].click()
    process_events()

    assert window.start_view.current_recipe_key() == "Custom A"
    assert config.get_config().last_recipe == "Custom A"


# ------------------------------------------------------------------ save as new / delete

def test_save_as_new_disambiguates_a_colliding_name(window, monkeypatch, process_events):
    from PyQt6.QtWidgets import QDialog
    from ui.recipe_editor import RecipeEditorDialog
    import config

    config.get_config().recipes = [
        {"name": "My recipe", "steps": ["transcribe"], "builtin_key": ""},
    ]
    window.start_view.refresh_recipe_chips()
    window.start_view.select_recipe("My recipe")
    process_events()

    def fake_exec(dialog):
        dialog._name_edit.setText("My recipe")
        dialog.result_action = "save_as_new"
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RecipeEditorDialog, "exec", fake_exec)
    window._open_recipe_editor()
    process_events()

    names = {e["name"] for e in config.get_config().recipes}
    assert names == {"My recipe", "My recipe (2)"}
    assert config.get_config().last_recipe == "My recipe (2)"
    assert window.start_view.current_recipe_key() == "My recipe (2)"


def test_save_upserts_an_existing_custom_recipe_in_place(window, monkeypatch, process_events):
    from PyQt6.QtWidgets import QDialog
    from ui.recipe_editor import RecipeEditorDialog
    import config

    config.get_config().recipes = [
        {"name": "My recipe", "steps": ["transcribe"], "builtin_key": ""},
    ]
    window.start_view.refresh_recipe_chips()
    window.start_view.select_recipe("My recipe")
    process_events()

    def fake_exec(dialog):
        dialog._checks["clean"].setChecked(True)
        dialog._name_edit.setText("My recipe")
        dialog.result_action = "save"
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RecipeEditorDialog, "exec", fake_exec)
    window._open_recipe_editor()
    process_events()

    recipes = config.get_config().recipes
    assert len(recipes) == 1
    assert recipes[0]["name"] == "My recipe"
    assert recipes[0]["steps"] == ["transcribe", "clean"]


def test_delete_removes_the_recipe_and_falls_back_to_transcript_only(
    window, monkeypatch, process_events,
):
    from PyQt6.QtWidgets import QDialog
    from ui.recipe_editor import RecipeEditorDialog
    from domain.recipe import TRANSCRIPT_ONLY
    import config

    config.get_config().recipes = [
        {"name": "Doomed", "steps": ["transcribe"], "builtin_key": ""},
    ]
    window.start_view.refresh_recipe_chips()
    window.start_view.select_recipe("Doomed")
    process_events()

    def fake_exec(dialog):
        dialog.result_action = "delete"
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RecipeEditorDialog, "exec", fake_exec)
    window._open_recipe_editor()
    process_events()

    assert config.get_config().recipes == []
    assert config.get_config().last_recipe == TRANSCRIPT_ONLY.builtin_key
    assert "Doomed" not in window.start_view._recipe_buttons


def test_delete_button_only_visible_for_an_already_saved_recipe(process_events):
    from domain.recipe import PODCAST_ARTICLE, Recipe
    from ui.recipe_editor import RecipeEditorDialog
    from ui.transcribe_options import TranscribeOptions

    options = TranscribeOptions()
    builtin_dialog = RecipeEditorDialog(options, PODCAST_ARTICLE, set())
    process_events()
    assert not builtin_dialog._delete_btn.isVisible()
    options.setParent(None)
    builtin_dialog.close()

    saved = Recipe(name="Already saved", steps=("transcribe",))
    options2 = TranscribeOptions()
    saved_dialog = RecipeEditorDialog(options2, saved, {"Already saved"})
    saved_dialog.show()
    process_events()
    assert saved_dialog._delete_btn.isVisibleTo(saved_dialog)
    options2.setParent(None)
    saved_dialog.close()


# ------------------------------------------------------------------ params override at transcription start

def test_start_transcription_uses_the_recipes_param_overrides(
    monkeypatch, tmp_path, process_events,
):
    import config
    from ui.main_window import MainWindow

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config(
        default_model="large-v3-turbo",
        recipes=[{
            "name": "Fast notes",
            "steps": ["transcribe"],
            "builtin_key": "",
            "params": {
                "model": "tiny", "performance_mode": "fast",
                "translate": True, "diarization": True,
            },
        }],
        last_recipe="Fast notes",
    ))

    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"\0" * 32)

    window = MainWindow()
    window.show()
    window.file_selector._set_file(str(clip))
    process_events()

    calls = []
    monkeypatch.setattr(
        window.transcriber, "transcribe", lambda **kwargs: calls.append(kwargs),
    )

    window._start_transcription()
    process_events()

    assert len(calls) == 1
    assert calls[0]["model_name"] == "tiny"
    assert calls[0]["translate"] is True
    assert calls[0]["enable_diarization"] is True

    window.close()
    process_events()


def test_start_transcription_falls_back_to_widget_defaults_without_overrides(
    monkeypatch, tmp_path, process_events,
):
    """A built-in recipe (no params) must behave exactly as before B4 —
    whatever the shared transcribe_options widget/Config already has."""
    import config
    from ui.main_window import MainWindow

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config(
        default_model="tiny", last_recipe="transcript_only",
    ))

    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"\0" * 32)

    window = MainWindow()
    window.show()
    window.file_selector._set_file(str(clip))
    process_events()

    calls = []
    monkeypatch.setattr(
        window.transcriber, "transcribe", lambda **kwargs: calls.append(kwargs),
    )

    window._start_transcription()
    process_events()

    assert len(calls) == 1
    assert calls[0]["model_name"] == "tiny"
    assert calls[0]["enable_diarization"] is False

    window.close()
    process_events()
