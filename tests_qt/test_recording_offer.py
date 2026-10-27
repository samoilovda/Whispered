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
