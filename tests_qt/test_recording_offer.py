"""Real-Qt tests for the "new recording finished" offer:
core/recording_watch.py notices a finished recording in the source folder
and MainWindow offers a recipe for it (ui/recording_offer_dialog.py).
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


def _finished_meeting(root):
    folder = root / "2026-10-27 10.00.00 Meeting"
    folder.mkdir()
    media = folder / "audio_only.m4a"
    media.write_bytes(b"\0" * 64)
    return media


def test_the_offer_is_off_by_default(window):
    assert window._recording_watch.folder == ""


def test_enabling_the_offer_watches_the_source_folder(window, monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "_config", config.Config(
        recording_offer_enabled=True, source_folder=str(tmp_path)))

    window._apply_recording_offer_config()
    assert window._recording_watch.folder == str(tmp_path)

    config.get_config().recording_offer_enabled = False
    window._apply_recording_offer_config()
    assert window._recording_watch.folder == ""


def test_the_service_reports_only_new_finished_recordings(monkeypatch, tmp_path, process_events):
    import core.recording_watch as rw

    monkeypatch.setattr(rw, "_SETTLE_SECONDS", 0.0)
    old = tmp_path / "old.mp3"
    old.write_bytes(b"\0" * 64)
    service = rw.RecordingWatchService()
    found: list[str] = []
    service.recording_ready.connect(found.append)
    service.set_folder(str(tmp_path))

    media = _finished_meeting(tmp_path)
    service._scan()  # first sighting starts the settle window
    service._scan()

    assert found == [str(media)]
    service.stop()


def test_a_new_recording_opens_one_offer_with_the_last_offer_recipe(window, tmp_path):
    import config

    config.get_config().recording_offer_recipe = "meeting_notes"
    media = _finished_meeting(tmp_path)

    window._on_new_recording_ready(str(media))
    window._on_new_recording_ready(str(media))

    dialog = window._recording_offer_dialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.recipe_combo.currentData() == "meeting_notes"
    assert window._recording_offers == []
    dialog.reject()


def test_run_starts_the_chosen_recipe_on_the_recording(window, monkeypatch, tmp_path):
    import config

    started: list[str | None] = []
    monkeypatch.setattr(window, "_start_transcription",
                        lambda: started.append(window.file_selector.get_file()))
    media = _finished_meeting(tmp_path)

    window._run_recording_offer(str(media), "podcast_article")

    assert started == [str(media)]
    assert window.start_view.current_recipe_key() == "podcast_article"
    assert config.get_config().recording_offer_recipe == "podcast_article"


def test_run_while_busy_queues_the_recording(window, monkeypatch, tmp_path):
    monkeypatch.setattr(window.transcriber, "is_busy", lambda: True)
    monkeypatch.setattr(window, "_start_transcription",
                        lambda: pytest.fail("must not start while busy"))
    media = _finished_meeting(tmp_path)

    window._run_recording_offer(str(media), "transcript_only")

    assert [item.filepath for item in window.batch_panel.processor.items] == [str(media)]


def test_later_keeps_the_recording_and_offers_it_again(window, monkeypatch, tmp_path):
    """Audit 2026-11-04 W07: "Later" used to mean "never"."""
    import config

    config.get_config().recording_offer_enabled = True
    media = _finished_meeting(tmp_path)
    window._on_new_recording_ready(str(media))
    assert config.get_config().recording_offers_waiting == [str(media)]

    window._recording_offer_dialog.later_btn.click()
    assert window._recording_offer_dialog is None
    assert config.get_config().recording_offers_waiting == [str(media)]

    window._reoffer_waiting_recordings()   # next start / an hour later
    assert window._recording_offer_dialog is not None
    assert window._recording_offer_dialog.path == str(media)
    window._recording_offer_dialog.dismiss_btn.click()
    assert config.get_config().recording_offers_waiting == []


def test_a_missing_waiting_recording_is_dropped(window, tmp_path):
    import config

    cfg = config.get_config()
    cfg.recording_offer_enabled = True
    cfg.recording_offers_waiting = [str(tmp_path / "gone.m4a")]
    window._reoffer_waiting_recordings()
    assert cfg.recording_offers_waiting == [] and window._recording_offer_dialog is None


def test_queueing_from_the_offer_leaves_the_start_screen_recipe_alone(window, monkeypatch, tmp_path):
    from application import queue_store

    monkeypatch.setattr(window.transcriber, "is_busy", lambda: True)
    before = window.start_view.current_recipe_key()
    media = _finished_meeting(tmp_path)
    seen = []
    real = window.batch_panel.snapshot_provider
    window.batch_panel.snapshot_provider = lambda key=None: seen.append(key) or real(key)

    window._run_recording_offer(str(media), "podcast_article")

    assert seen == ["podcast_article"]
    assert window.start_view.current_recipe_key() == before
    assert queue_store  # the snapshot path is the real one


def test_no_folder_means_nothing_is_watched(window, monkeypatch):
    """Audit 2026-11-04 W25: ~/Documents/Zoom is never read unasked."""
    import config

    monkeypatch.setattr(config, "_config", config.Config(recording_offer_enabled=True, source_folder=""))
    window._apply_recording_offer_config()
    assert window._recording_watch.folder == ""


def test_switching_the_offer_on_fills_in_zoom_s_folder(process_events, monkeypatch, tmp_path):
    import config
    from domain.recording_settle import default_recording_folder
    from ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    dialog = SettingsDialog()
    dialog._recording_offer_chk.setChecked(True)
    assert dialog._source_folder_edit.text() == default_recording_folder()
    dialog.close()
    process_events()


def test_watch_folder_files_take_the_watch_folder_recipe(window, monkeypatch, tmp_path):
    """Audit 2026-11-04 W26: not whatever the start screen shows then."""
    import config

    config.get_config().watch_folder_recipe = "meeting_notes"
    window.start_view.select_recipe("podcast_article")
    seen = []
    real = window.batch_panel.snapshot_provider
    window.batch_panel.snapshot_provider = lambda key=None: seen.append(key) or real(key)
    media = tmp_path / "new.mp3"
    media.write_bytes(b"\0" * 64)

    window._on_watch_folder_file_found(str(media))

    assert seen == ["meeting_notes"]


def test_settings_store_the_watch_folder_recipe(process_events, monkeypatch, tmp_path):
    import config
    from ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    dialog = SettingsDialog()
    combo = dialog._watch_recipe_combo
    assert combo.currentData() == "transcript_only"
    combo.setCurrentIndex(combo.findData("book"))
    dialog._save_values()
    assert config.get_config().watch_folder_recipe == "book"
    dialog.close()
    process_events()


def test_the_folder_source_names_the_recipe_the_queue_runs(window):
    """Audit 2026-11-04 W08: the hint said recipe steps were not applied."""
    from core.i18n import tr
    from domain.recipe import BUILTIN_RECIPES_BY_KEY
    from ui.option_labels import recipe_label

    window.start_view.select_recipe("meeting_notes")
    label = recipe_label(BUILTIN_RECIPES_BY_KEY["meeting_notes"])
    assert label in window._folder_hint_label.text()
    assert window._folder_hint_label.text() == tr("draft_folder_hint", recipe=label)
