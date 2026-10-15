"""The recipe's "diarize" step run on its own (application/steps.py's
``_diarize_runner``) and the record version it is saved as
(application/records.py's ``save_record_version``).

A fake ``Diarizer`` stands in for sherpa-onnx, so no models are needed.
"""

from __future__ import annotations

import dataclasses

import pytest

import config
import diarizer
from application.job_engine import JobEngine
from application.records import save_record_version
from application.steps import STEP_REGISTRY, build_job_spec, build_runners, build_step_context
from domain.diarization import DiarizationResult, SpeakerSegment
from domain.job import StepStatus
from domain.transcription import Segment, TranscriptionResult


def _result(*speakers) -> TranscriptionResult:
    speakers = speakers or (None, None)
    return TranscriptionResult(
        segments=[
            Segment(start=0.0, end=2.0, text="Hello there.", speaker=speakers[0]),
            Segment(start=2.0, end=5.0, text="Hi, how are you?", speaker=speakers[1]),
        ],
        language="en",
        duration=5.0,
        speaker_names={"Speaker 1": "Anna"},
    )


class _FakeDiarizer:
    calls: list = []
    available = True
    cancel = False

    def is_available(self) -> bool:
        return self.available

    def diarize(self, audio_path, num_speakers=None, on_progress=None, is_cancelled=None):
        type(self).calls.append({
            "path": audio_path, "num_speakers": num_speakers,
            "on_progress": on_progress, "is_cancelled": is_cancelled,
        })
        if on_progress:
            on_progress(50, "Finding speaker turns...")
        if self.cancel:
            raise diarizer.DiarizationCancelled()
        return DiarizationResult(
            segments=[
                SpeakerSegment(0.0, 2.1, "Speaker 1"),
                SpeakerSegment(2.1, 5.0, "Speaker 2"),
            ],
            num_speakers=2,
            duration=5.0,
        )


@pytest.fixture
def fake_diarizer(monkeypatch):
    _FakeDiarizer.calls = []
    _FakeDiarizer.available = True
    _FakeDiarizer.cancel = False
    monkeypatch.setattr(diarizer, "Diarizer", _FakeDiarizer)
    monkeypatch.setattr(config, "_config", config.Config())
    return _FakeDiarizer


@pytest.fixture
def media(tmp_path):
    path = tmp_path / "call.wav"
    path.write_bytes(b"RIFF")
    return path


def _context(tmp_path, source, result, **kwargs):
    return build_step_context(str(source), result, 7, out_dir=tmp_path / "out", **kwargs)


def _run(context):
    return STEP_REGISTRY["diarize"].make_runner(context)()


def test_a_transcript_with_speakers_passes_through_untouched(tmp_path, media, fake_diarizer):
    result = _result("Speaker 1", "Speaker 2")

    assert _run(_context(tmp_path, media, result)) is result
    assert fake_diarizer.calls == []


def test_labels_a_copy_and_keeps_the_users_renames(tmp_path, media, fake_diarizer):
    result = _result()
    progress = []

    context = dataclasses.replace(
        _context(tmp_path, media, result), on_progress=lambda pct, msg: progress.append(pct),
    )

    labelled = _run(context)

    assert labelled is not result
    assert [seg.speaker for seg in labelled.segments] == ["Speaker 1", "Speaker 2"]
    assert [seg.speaker for seg in result.segments] == [None, None]
    assert labelled.speaker_names == {"Speaker 1": "Anna"}
    assert [seg.text for seg in labelled.segments] == [seg.text for seg in result.segments]
    assert fake_diarizer.calls[0]["path"] == str(media)
    assert progress == [50]


def test_uses_the_configured_speaker_count_and_cancel_flag(tmp_path, media, fake_diarizer):
    config._config.default_num_speakers = 3
    flag = lambda: False  # noqa: E731

    _run(_context(tmp_path, media, _result(), is_cancelled=flag))

    call = fake_diarizer.calls[0]
    assert call["num_speakers"] == 3
    assert call["is_cancelled"] is flag


def test_missing_source_media_fails_clearly(tmp_path, fake_diarizer):
    with pytest.raises(FileNotFoundError, match="source audio"):
        _run(_context(tmp_path, tmp_path / "gone.wav", _result()))
    with pytest.raises(FileNotFoundError, match="source audio"):
        _run(_context(tmp_path, "", _result()))
    assert fake_diarizer.calls == []


def test_without_sherpa_onnx_the_step_fails_clearly(tmp_path, media, fake_diarizer):
    fake_diarizer.available = False

    with pytest.raises(RuntimeError, match="sherpa-onnx"):
        _run(_context(tmp_path, media, _result()))


def test_a_cancelled_diarization_fails_the_step(tmp_path, media, fake_diarizer):
    fake_diarizer.cancel = True

    with pytest.raises(RuntimeError, match="cancelled"):
        _run(_context(tmp_path, media, _result()))


def test_job_engine_reports_the_labelled_result(tmp_path, media, fake_diarizer):
    context = _context(tmp_path, media, _result())
    spec = build_job_spec("diarize-only", ("diarize",))

    run = JobEngine().run(spec, build_runners(context, ("diarize",)))

    outcome = run.outcomes["diarize"]
    assert outcome.status is StepStatus.SUCCEEDED
    assert outcome.result.segments[1].speaker == "Speaker 2"


def test_job_engine_reports_a_missing_source_as_a_failure(tmp_path, fake_diarizer):
    context = _context(tmp_path, tmp_path / "gone.wav", _result())
    spec = build_job_spec("diarize-only", ("diarize",))

    run = JobEngine().run(spec, build_runners(context, ("diarize",)))

    outcome = run.outcomes["diarize"]
    assert outcome.status is StepStatus.FAILED
    assert "source audio" in outcome.error


# ---------------------------------------------------------------- history

def test_save_record_version_writes_a_new_version_with_speaker_names(
    tmp_path, monkeypatch, fake_diarizer,
):
    from core.history import HistoryStore

    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    original = _result()
    record_id = store.add(original, source_path="call.wav", model="m", speaker_names={})
    store.save_current_revision(record_id, original, {}, keep=20)
    store.set_artifacts(record_id, ["article", "transcript"])

    labelled = _result("Speaker 1", "Speaker 2")
    assert save_record_version(record_id, labelled)

    record = store.get_record(record_id)
    assert [seg["speaker"] for seg in record["payload"]["segments"]] == ["Speaker 1", "Speaker 2"]
    assert record["payload"]["speaker_names"] == {"Speaker 1": "Anna"}
    assert len(store.list_transcript_revisions(record_id)) == 2
    # The record's badges survive: its articles are still on disk.
    assert record["artifacts"] == ["article", "transcript"]


def test_save_record_version_without_a_record_does_nothing():
    assert save_record_version(None, _result()) is False
