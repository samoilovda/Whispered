"""Real-Qt tests for MainWindow's watch-folder wiring (B5b,
docs/IMPROVEMENT_PLAN_2026-08.ru.md): WatchFolderService.file_found joins
the same queue a multi-file drop does (B5a).
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


def test_watch_folder_is_off_by_default(window):
    assert window._watch_folder_service._folder == ""


def test_a_disabled_watch_folder_config_does_not_start_watching(
    window, monkeypatch, tmp_path,
):
    import config

    cfg = config.Config(watch_folder=str(tmp_path), watch_folder_enabled=False)
    monkeypatch.setattr(config, "_config", cfg)

    window._apply_watch_folder_config()

    assert window._watch_folder_service._folder == ""


def test_enabling_watch_folder_in_config_starts_watching_that_folder(
    window, monkeypatch, tmp_path,
):
    import config

    cfg = config.Config(watch_folder=str(tmp_path), watch_folder_enabled=True)
    monkeypatch.setattr(config, "_config", cfg)

    window._apply_watch_folder_config()

    assert window._watch_folder_service._folder == str(tmp_path)


def test_a_file_found_by_the_watcher_is_queued_and_opens_the_overlay(
    window, tmp_path, process_events,
):
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"\0" * 32)

    window._on_watch_folder_file_found(str(clip))
    process_events()

    assert window.batch_panel.processor.count == 1
    assert window.batch_panel.processor.items[0].filepath == str(clip)
    assert window.status_bar._overlay.isVisible()
