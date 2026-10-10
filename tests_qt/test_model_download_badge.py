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


def _run_download(monkeypatch, models_dir, process_events, served: bytes, expected: bytes):
    """DownloadWorker for a manifest model, the network replaced by *served*."""
    import hashlib
    import time
    from unittest.mock import MagicMock

    from core.model_manifest import ModelEntry
    from ui.model_downloader import DownloadWorker

    entry = ModelEntry(
        key="whisper-test", url="https://example.invalid/ggml-test.bin",
        size_bytes=len(expected), sha256=hashlib.sha256(expected).hexdigest(),
        license="MIT", filename="ggml-test.bin",
    )
    monkeypatch.setitem(MANIFEST, "whisper-test", entry)
    response = MagicMock()
    response.headers = {"content-length": str(len(served))}
    response.iter_content.return_value = [served]
    response.raise_for_status.return_value = None
    monkeypatch.setattr("core.model_repository.requests.get", lambda *a, **k: response)

    target = models_dir / "ggml-test.bin"
    outcome: list = []
    worker = DownloadWorker(entry.url, str(target), manifest_key="whisper-test")
    worker.finished.connect(lambda ok, detail: outcome.append((ok, detail)))
    worker.start()
    deadline = time.monotonic() + 5
    while not outcome and time.monotonic() < deadline:
        process_events()
    worker.wait(2000)
    return outcome, target


def test_download_of_a_manifest_model_is_verified(monkeypatch, models_dir, process_events):
    outcome, target = _run_download(
        monkeypatch, models_dir, process_events, served=b"model", expected=b"model")
    assert outcome == [(True, str(target))]
    assert target.read_bytes() == b"model"


def test_a_corrupted_download_is_rejected_and_not_kept(monkeypatch, models_dir, process_events):
    outcome, target = _run_download(
        monkeypatch, models_dir, process_events, served=b"mode!", expected=b"model")
    assert outcome == [(False, tr("download_error_integrity"))]
    assert not target.exists()
    assert not (models_dir / "ggml-test.bin.download").exists()


def test_every_offered_whisper_model_has_a_verified_manifest_entry():
    from core.model_manifest import whisper_entry
    from utils import WHISPER_MODELS

    for key, _label in WHISPER_MODELS:
        entry = whisper_entry(key)
        assert entry is not None, key
        assert entry.size_bytes and len(entry.sha256) == 64, key
