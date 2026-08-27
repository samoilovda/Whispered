"""Real-Qt tests for the model combo's downloaded-state suffix (B10, see
docs/IMPROVEMENT_PLAN_2026-08.ru.md) on both places it appears:
ui/transcribe_options.py::TranscribeOptions and
ui/live_setup_panel.py::LiveSetupPanel.
"""

from __future__ import annotations

import pytest

from core.i18n import load_locale, tr
from core.model_manifest import MANIFEST


@pytest.fixture(autouse=True)
def _pin_english_locale():
    load_locale("en")
    yield


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.get_models_dir", lambda: str(tmp_path))
    return tmp_path


def test_transcribe_options_model_combo_shows_the_suffix_in_item_text(
    models_dir, process_events,
):
    from ui.transcribe_options import TranscribeOptions

    entry = MANIFEST["whisper-tiny"]
    (models_dir / "ggml-tiny.bin").write_bytes(b"\0" * entry.size_bytes)

    panel = TranscribeOptions()
    process_events()

    tiny_index = panel.model_combo.findData("tiny")
    base_index = panel.model_combo.findData("base")
    assert tr("model_state_downloaded") in panel.model_combo.itemText(tiny_index)
    assert tr("model_state_not_downloaded") in panel.model_combo.itemText(base_index)

    panel.close()


def test_refresh_model_state_picks_up_a_newly_downloaded_model(
    models_dir, process_events,
):
    from ui.transcribe_options import TranscribeOptions

    panel = TranscribeOptions()
    process_events()
    tiny_index = panel.model_combo.findData("tiny")
    assert tr("model_state_not_downloaded") in panel.model_combo.itemText(tiny_index)

    entry = MANIFEST["whisper-tiny"]
    (models_dir / "ggml-tiny.bin").write_bytes(b"\0" * entry.size_bytes)
    panel.refresh_model_state()
    process_events()

    tiny_index = panel.model_combo.findData("tiny")
    assert tr("model_state_downloaded") in panel.model_combo.itemText(tiny_index)

    panel.close()


def test_refresh_model_state_preserves_the_current_selection(models_dir, process_events):
    from ui.transcribe_options import TranscribeOptions

    panel = TranscribeOptions()
    process_events()
    idx = panel.model_combo.findData("small")
    panel.model_combo.setCurrentIndex(idx)
    process_events()

    panel.refresh_model_state()
    process_events()

    assert panel.model_combo.currentData() == "small"
    panel.close()


def test_refresh_model_state_does_not_re_persist_the_selection_as_a_side_effect(
    models_dir, monkeypatch, process_events,
):
    """blockSignals() around the rebuild must keep currentIndexChanged
    (and therefore _persist -> Config write) from firing just because the
    combo was cleared and refilled."""
    from ui.transcribe_options import TranscribeOptions

    panel = TranscribeOptions()
    process_events()

    calls = []
    monkeypatch.setattr(panel, "_persist", lambda: calls.append(True))
    panel.refresh_model_state()
    process_events()

    assert calls == []
    panel.close()


def test_live_setup_panel_model_combo_shows_the_suffix(models_dir, process_events):
    from ui.live_setup_panel import LiveSetupPanel

    entry = MANIFEST["whisper-tiny"]
    (models_dir / "ggml-tiny.bin").write_bytes(b"\0" * entry.size_bytes)

    panel = LiveSetupPanel()
    process_events()

    tiny_index = panel.model_combo.findData("tiny")
    base_index = panel.model_combo.findData("base")
    assert tr("model_state_downloaded") in panel.model_combo.itemText(tiny_index)
    assert tr("model_state_not_downloaded") in panel.model_combo.itemText(base_index)

    panel.close()


def test_live_setup_panel_refresh_model_state_picks_up_a_new_download(
    models_dir, process_events,
):
    from ui.live_setup_panel import LiveSetupPanel

    panel = LiveSetupPanel()
    process_events()

    entry = MANIFEST["whisper-tiny"]
    (models_dir / "ggml-tiny.bin").write_bytes(b"\0" * entry.size_bytes)
    panel.refresh_model_state()
    process_events()

    tiny_index = panel.model_combo.findData("tiny")
    assert tr("model_state_downloaded") in panel.model_combo.itemText(tiny_index)

    panel.close()


# ------------------------------------------------------------------ MainWindow wiring

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


def test_settings_applied_refreshes_every_model_combo(
    window, models_dir, process_events,
):
    """MainWindow._on_settings_applied (B10 item 3) must pick up a model
    downloaded since the combos were last built, without a restart."""
    tiny_index = window.transcribe_options.model_combo.findData("tiny")
    assert tr("model_state_not_downloaded") in window.transcribe_options.model_combo.itemText(tiny_index)

    entry = MANIFEST["whisper-tiny"]
    (models_dir / "ggml-tiny.bin").write_bytes(b"\0" * entry.size_bytes)
    window._on_settings_applied()
    process_events()

    tiny_index = window.transcribe_options.model_combo.findData("tiny")
    assert tr("model_state_downloaded") in window.transcribe_options.model_combo.itemText(tiny_index)

    tiny_index = window.course_capture_panel.setup.model_combo.findData("tiny")
    assert tr("model_state_downloaded") in window.course_capture_panel.setup.model_combo.itemText(tiny_index)
    tiny_index = window.live_view.setup.model_combo.findData("tiny")
    assert tr("model_state_downloaded") in window.live_view.setup.model_combo.itemText(tiny_index)


def test_opening_the_recipe_editor_refreshes_the_model_combo(
    window, models_dir, monkeypatch, process_events,
):
    """The recipe editor borrows the same long-lived TranscribeOptions
    instance rather than rebuilding it — a download since it was last
    open must still show up without going through Settings."""
    from PyQt6.QtWidgets import QDialog

    monkeypatch.setattr(
        "ui.main_window.RecipeEditorDialog.exec",
        lambda self: QDialog.DialogCode.Rejected,
    )

    tiny_index = window.transcribe_options.model_combo.findData("tiny")
    assert tr("model_state_not_downloaded") in window.transcribe_options.model_combo.itemText(tiny_index)

    entry = MANIFEST["whisper-tiny"]
    (models_dir / "ggml-tiny.bin").write_bytes(b"\0" * entry.size_bytes)
    window._open_recipe_editor()
    process_events()

    tiny_index = window.transcribe_options.model_combo.findData("tiny")
    assert tr("model_state_downloaded") in window.transcribe_options.model_combo.itemText(tiny_index)
