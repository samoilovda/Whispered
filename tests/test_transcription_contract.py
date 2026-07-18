"""Golden contracts for the stable batch transcription data model.

Live transcription must end at this boundary: old consumers receive ordinary
``Segment`` and ``TranscriptionResult`` instances, never mutable live state.
The module is loaded under an isolated name because several legacy exporter
tests intentionally replace ``sys.modules['transcriber']`` with a small fake.
"""

from __future__ import annotations

import importlib.util
import json
import sys
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
