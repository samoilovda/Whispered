"""Real-Qt tests for audit 2026-11-04 W01: one whisper model at a time.
The single-file transcriber and the queue each own a Transcriber; neither
may start while the other (or Live / Course Capture) has a model loaded."""

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


def _media(tmp_path):
    media = tmp_path / "talk.mp3"
    media.write_bytes(b"\0" * 64)
    return media


def test_nobody_holds_the_model_when_idle(window):
    assert window.operations.asr_holder() is None


def test_a_running_queue_takes_the_new_file_instead_of_a_second_model(window, monkeypatch, tmp_path):
    monkeypatch.setattr(type(window.batch_panel.processor), "is_processing",
                        property(lambda self: True))
    monkeypatch.setattr(window.transcriber, "transcribe",
                        lambda **_: pytest.fail("second whisper process"))
    monkeypatch.setattr(window, "_ensure_whisper_model", lambda _m: True)
    media = _media(tmp_path)
    window.file_selector._set_file(str(media))

    window._start_transcription()

    assert window.operations.asr_holder().key == "batch"
    assert str(media) in [item.filepath for item in window.batch_panel.processor.items]


def test_the_queue_does_not_start_beside_a_transcription(window, monkeypatch, tmp_path):
    monkeypatch.setattr(window.transcriber, "is_busy", lambda: True)
    monkeypatch.setattr(window.batch_panel, "start_processing",
                        lambda **_: pytest.fail("second whisper process"))
    monkeypatch.setattr(window, "_ensure_whisper_model", lambda _m: True)

    window._start_batch_processing()

    assert window.operations.asr_holder().key == "transcribe"


def test_the_offer_queues_while_the_queue_transcribes(window, monkeypatch, tmp_path):
    monkeypatch.setattr(type(window.batch_panel.processor), "is_processing",
                        property(lambda self: True))
    monkeypatch.setattr(window, "_start_transcription",
                        lambda: pytest.fail("must not start beside the queue"))
    media = _media(tmp_path)

    window._run_recording_offer(str(media), "transcript_only")

    assert str(media) in [item.filepath for item in window.batch_panel.processor.items]


def test_live_does_not_start_beside_a_transcription(window, monkeypatch):
    monkeypatch.setattr(window.transcriber, "is_busy", lambda: True)
    monkeypatch.setattr(window.live_runtime, "start",
                        lambda **_: pytest.fail("second whisper model"))
    monkeypatch.setattr(window, "_ensure_whisper_model", lambda _m: True)
    monkeypatch.setattr(window.live_view, "selected_target", lambda: object())

    window.live_session.start()


def test_course_capture_asks_the_window(window, monkeypatch):
    monkeypatch.setattr(window.transcriber, "is_busy", lambda: True)
    assert window.course_capture_panel.asr_busy() != ""
