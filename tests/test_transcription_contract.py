"""Golden contracts for the stable batch transcription data model.

Live transcription must end at this boundary: old consumers receive ordinary
``Segment`` and ``TranscriptionResult`` instances, never mutable live state.
The module is loaded under an isolated name because several legacy exporter
tests intentionally replace ``sys.modules['transcriber']`` with a small fake.
"""

from __future__ import annotations

import importlib.util
import json
import queue
import sys
import types
import wave
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = Path(__file__).with_name("fixtures") / "batch_golden_result.json"


def _load_transcriber_contract_module():
    module_name = "_whispered_transcriber_contract"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing

    spec = importlib.util.spec_from_file_location(module_name, _PROJECT_ROOT / "transcriber.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _result_from_fixture():
    model = _load_transcriber_contract_module()
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    segments = [
        model.Segment(
            start=item["start"],
            end=item["end"],
            text=item["text"],
            speaker=item["speaker"],
            words=[model.Word(**word) for word in item["words"]],
        )
        for item in payload["segments"]
    ]
    return model.TranscriptionResult(
        segments=segments,
        language=payload["language"],
        duration=payload["duration"],
        speaker_names=payload["speaker_names"],
    )


def test_golden_segment_contract_preserves_timing_speakers_and_words():
    result = _result_from_fixture()

    first = result.segments[0]
    assert (first.start, first.end, first.text, first.speaker) == (
        0.0,
        3.2,
        " Привет, мир! ",
        "Speaker 1",
    )
    assert [(word.start, word.end, word.text) for word in first.words] == [
        (0.0, 0.8, " Привет,"),
        (0.9, 1.4, " мир!"),
    ]


def test_golden_result_contract_keeps_plain_text_and_speaker_names():
    result = _result_from_fixture()

    assert result.language == "ru"
    assert result.duration == 12.5
    assert result.full_text == "Привет, мир! Hello from Whispered. Финальная реплика."
    assert result.speaker_label("Speaker 1") == "Анна"
    assert result.speaker_label("Speaker 2") == "Mikhail"
    assert result.speaker_label(None) is None


def test_cpu_selection_reaches_whisper_context(monkeypatch, tmp_path):
    """The device badge must control whisper.cpp, not just presentation."""
    model = _load_transcriber_contract_module()
    wav_path = tmp_path / "one-second.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 16_000)

    captured = {}

    class FakeSegment:
        t0 = 0
        t1 = 100
        text = " hello"

    class FakeModel:
        def __init__(self, _name, **kwargs):
            captured.update(kwargs)

        def auto_detect_language(self, _path):
            return (("en", 0.99), None)

        def transcribe(self, _path, new_segment_callback=None, **_params):
            segment = FakeSegment()
            if new_segment_callback:
                new_segment_callback(segment)
            return [segment]

    package = types.ModuleType("pywhispercpp")
    package.__path__ = []
    model_module = types.ModuleType("pywhispercpp.model")
    model_module.Model = FakeModel
    monkeypatch.setitem(sys.modules, "pywhispercpp", package)
    monkeypatch.setitem(sys.modules, "pywhispercpp.model", model_module)
    monkeypatch.setattr("utils.get_models_dir", lambda: str(tmp_path))

    events = queue.Queue()
    model._run_transcription_process(
        str(wav_path), "base", "auto", False, 2, False, None, events,
        use_gpu=False,
    )

    assert captured["context_params"] == {"use_gpu": False}
    queued = []
    while not events.empty():
        queued.append(events.get())
    assert any(event[0] == "result" for event in queued)


def test_batch_fragments_merge_until_sentence_boundary():
    model = _load_transcriber_contract_module()
    segments = [
        model.Segment(0.0, 0.8, "This is"),
        model.Segment(0.9, 1.7, "a sentence."),
        model.Segment(1.8, 2.5, "Next one."),
    ]

    merged = model._coalesce_batch_segments(segments)

    assert [(item.start, item.end, item.text) for item in merged] == [
        (0.0, 1.7, "This is a sentence."),
        (1.8, 2.5, "Next one."),
    ]


def test_batch_merge_preserves_text_timing_and_words():
    model = _load_transcriber_contract_module()
    word_a = model.Word(0.0, 0.4, " hello")
    word_b = model.Word(0.5, 0.9, " world")
    segments = [
        model.Segment(0.0, 0.4, " hello ", words=[word_a]),
        model.Segment(0.5, 0.9, " world ", words=[word_b]),
    ]
    before = model.TranscriptionResult(segments, "en", 0.9).full_text

    merged = model._coalesce_batch_segments(segments)

    assert model.TranscriptionResult(merged, "en", 0.9).full_text == before
    assert (merged[0].start, merged[0].end) == (0.0, 0.9)
    assert merged[0].words == [word_a, word_b]


def test_batch_merge_respects_pause_overlap_and_speaker_boundaries():
    model = _load_transcriber_contract_module()
    segments = [
        model.Segment(0.0, 1.0, "one", "A"),
        model.Segment(2.0, 3.0, "pause", "A"),
        model.Segment(3.1, 4.0, "speaker", "B"),
        model.Segment(3.9, 4.5, "overlap", "B"),
    ]

    merged = model._coalesce_batch_segments(segments)

    assert [item.text for item in merged] == ["one", "pause", "speaker", "overlap"]
