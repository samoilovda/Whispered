"""Real-Qt tests for "Detect Speakers" on an existing transcript
(ui/speaker_detection_controller.py): the diarize step runs in a JobRunner
thread, the labelled transcript reaches the transcript view through
DocumentSession and is saved as a new version of the record. A fake
Diarizer stands in for sherpa-onnx.
"""

from __future__ import annotations

import time

import pytest

import diarizer
from core.history import HistoryStore
from domain.diarization import DiarizationResult, SpeakerSegment
from transcriber import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[
            Segment(0.0, 2.0, "Hello there.", speaker=None),
            Segment(2.0, 5.0, "Hi, how are you?", speaker=None),
        ],
        language="en",
        duration=5.0,
    )


class _FakeDiarizer:
    def is_available(self) -> bool:
        return True

    def diarize(self, audio_path, num_speakers=None, on_progress=None, is_cancelled=None):
        return DiarizationResult(
            segments=[SpeakerSegment(0.0, 2.1, "Speaker 1"), SpeakerSegment(2.1, 5.0, "Speaker 2")],
            num_speakers=2,
            duration=5.0,
        )


@pytest.fixture
def window(monkeypatch, tmp_path, process_events):
    import config
    from ui.main_window import MainWindow

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    monkeypatch.setattr("core.paths.output_dir", lambda: tmp_path / "output")

    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    monkeypatch.setattr(diarizer, "Diarizer", _FakeDiarizer)
    monkeypatch.setattr(diarizer, "is_installed", lambda: True)

    win = MainWindow()
    yield win
    win.close()
    process_events()


def _open_record(window, tmp_path, process_events):
    media = tmp_path / "call.wav"
    media.write_bytes(b"RIFF")
    result = _result()
    window._save_to_history(result, source_path=str(media), model="m", speaker_names={})
    window.transcript_view.set_result(result)
    window._document_session.apply_result(result)
    process_events()
    return result


def _wait_for_detection(window, process_events, timeout=10.0):
    deadline = time.monotonic() + timeout
    while window.speaker_detection.job is not None and time.monotonic() < deadline:
        process_events()
    assert window.speaker_detection.job is None, "speaker detection did not finish"


def test_detect_speakers_labels_the_open_record(window, tmp_path, process_events):
    from core.history import get_history_store

    _open_record(window, tmp_path, process_events)
    record_id = window._last_record_id

    window.speaker_detection.start()
    _wait_for_detection(window, process_events)

    text = window.transcript_view.get_text()
    assert "Speaker 1" in text and "Speaker 2" in text
    assert [s.speaker for s in window._current_result.segments] == ["Speaker 1", "Speaker 2"]
    store = get_history_store()
    payload = store.get_record(record_id)["payload"]
    assert [s["speaker"] for s in payload["segments"]] == ["Speaker 1", "Speaker 2"]
    assert len(store.list_transcript_revisions(record_id)) == 2


def test_the_users_renames_show_in_the_transcript(window, tmp_path, process_events):
    result = _open_record(window, tmp_path, process_events)
    result.speaker_names = {"Speaker 1": "Anna"}

    window.speaker_detection.start()
    _wait_for_detection(window, process_events)

    text = window.transcript_view.get_text()
    assert "Anna" in text and "Speaker 2" in text


def test_menu_action_is_in_the_command_palette(window):
    from core.i18n import tr

    assert tr("menu_detect_speakers") in [a.text() for a in window._palette_menu_actions]


def test_a_recipe_runs_diarize_result_reaches_the_transcript(window, tmp_path, process_events):
    import copy

    result = _open_record(window, tmp_path, process_events)
    labelled = copy.deepcopy(result)
    labelled.segments[0].speaker = "Speaker 1"
    labelled.segments[1].speaker = "Speaker 2"

    window._show_step_result("transcript", labelled)
    process_events()

    assert "Speaker 2" in window.transcript_view.get_text()


def test_a_transcript_edited_meanwhile_is_not_overwritten(window, tmp_path, process_events):
    import copy

    result = _open_record(window, tmp_path, process_events)
    labelled = copy.deepcopy(result)
    labelled.segments[0].speaker = "Speaker 1"
    edited = copy.deepcopy(result)
    edited.segments[0].text = "Hello, there."
    window._document_session.apply_result(edited)

    assert not window.speaker_detection.apply(labelled, window._last_record_id)
    assert window._current_result is edited


def test_without_the_source_media_nothing_runs(window, tmp_path, process_events):
    _open_record(window, tmp_path, process_events)
    (tmp_path / "call.wav").unlink()

    window.speaker_detection.start()

    assert window.speaker_detection.job is None
