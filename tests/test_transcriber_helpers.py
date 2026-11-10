"""Regression tests for small transcription helpers without invoking Whisper."""

import os
import sys
from unittest.mock import patch

import pytest

# Other isolated test modules install a lightweight transcriber stub.  This
# regression test intentionally exercises the real helper.
sys.modules.pop("transcriber", None)
from transcriber import FFmpegUnavailableError, MediaConversionError, _convert_to_wav


class _CompletedProcess:
    returncode = 1
    stderr = "corrupt input"


def test_convert_to_wav_uses_unique_temporary_paths_and_cleans_failed_output(tmp_path):
    """Two conversions of same basename must never share a predictable WAV."""
    outputs = []

    def failed_run(command, **kwargs):
        outputs.append(command[-1])
        return _CompletedProcess()

    with patch("core.external_tools.resolve_tool", return_value="ffmpeg"), \
         patch.object(
             _convert_to_wav.__globals__["subprocess"], "run", side_effect=failed_run
         ):
        with pytest.raises(MediaConversionError, match="corrupt input"):
            _convert_to_wav(str(tmp_path / "same-name.mp4"))
        with pytest.raises(MediaConversionError, match="corrupt input"):
            _convert_to_wav(str(tmp_path / "same-name.mp4"))

    assert outputs[0] != outputs[1]
    assert all(not os.path.exists(output) for output in outputs)


def test_convert_to_wav_distinguishes_missing_ffmpeg():
    with patch("core.external_tools.resolve_tool", return_value=None), \
         pytest.raises(FFmpegUnavailableError, match="not installed"):
        _convert_to_wav("meeting.mp4")


def test_child_errors_are_translated_in_the_app_process(monkeypatch):
    """Audit 2026-11-04 W13: the child's errors were English in every UI."""
    import transcriber
    from core import i18n

    monkeypatch.setattr(i18n, "tr", lambda key, **kw: f"{key}:{sorted(kw.items())}")
    assert transcriber._error_text(("error", "transcribe_error_memory", {"model": "tiny"})) \
        == "transcribe_error_memory:[('model', 'tiny')]"
    assert transcriber._error_text(("error", "raw engine text")) == "raw engine text"


def test_language_fallback_progress_is_localized_and_does_not_step_back():
    from pathlib import Path

    from ui.transcription_progress import localized_progress
    import transcriber

    assert localized_progress(transcriber.LANGUAGE_FALLBACK_MESSAGE) != transcriber.LANGUAGE_FALLBACK_MESSAGE
    source = Path(transcriber.__file__).read_text(encoding="utf-8")
    assert "('progress', 12," not in source   # detection used to report 12 % after 15 %


def test_asr_metrics_use_recording_duration_including_trailing_silence(tmp_path, monkeypatch):
    import queue
    import wave
    from types import ModuleType, SimpleNamespace

    from core.asr_metrics import TimedWord
    from tools.bench_asr import score
    from transcriber import _run_transcription_process

    path = tmp_path / "synthetic.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(bytes(10 * 16000 * 2))
    binding = ModuleType("pywhispercpp.model")
    binding.Model = lambda *args, **kwargs: SimpleNamespace(
        transcribe=lambda *args, **kwargs: [
            SimpleNamespace(t0=100, t1=150, text="Тест."),
            SimpleNamespace(t0=200, t1=250, text="Лишнее."),
        ],
    )
    monkeypatch.setitem(sys.modules, "pywhispercpp.model", binding)
    monkeypatch.setattr("utils.get_models_dir", lambda: str(tmp_path))

    messages = queue.Queue()
    _run_transcription_process(str(path), "fake", "ru", False, 1, False, None, messages)
    result = next(msg[1] for msg in messages.queue if msg[0] == "result")
    row = score(result, 1.0, "Тест.", [TimedWord(1.0, 1.5, "Тест.")])
    assert result.duration == 10
    assert row["rtf"] == 0.1
    assert row["silence_words"] == 1
    assert set(row["stage_seconds"]) == {"model_load", "decode"}


def test_file_vad_needs_both_a_model_and_a_compatible_binding(tmp_path, monkeypatch):
    from types import ModuleType

    from core.model_manifest import MANIFEST, VAD_MODEL_KEY
    from transcriber import _vad_params

    constants = ModuleType("pywhispercpp.constants")
    constants.PARAMS_SCHEMA = {}
    monkeypatch.setitem(sys.modules, "pywhispercpp.constants", constants)
    assert _vad_params(str(tmp_path)) == {}
    constants.PARAMS_SCHEMA.update(vad={}, vad_model_path={})
    assert _vad_params(str(tmp_path)) == {}
    entry = MANIFEST[VAD_MODEL_KEY]
    path = tmp_path / entry.filename
    path.write_bytes(bytes(entry.size_bytes))
    assert _vad_params(str(tmp_path)) == {"vad": True, "vad_model_path": str(path)}


def test_language_windows_skip_silent_intro_and_report_disagreement(tmp_path):
    import wave
    from types import SimpleNamespace

    from transcriber import _detect_language_in_windows

    path = tmp_path / "windows.wav"
    quiet = bytes(5 * 16000 * 2)
    audible = b"\x00\x10\x00\xf0" * (15 * 16000 // 2)
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(quiet + audible)
    votes = iter([(("en", 0.7), {}), (("ru", 0.9), {})])
    model = SimpleNamespace(auto_detect_language=lambda pcm: next(votes))
    assert _detect_language_in_windows(model, str(path)) == ("ru", 0.9, True)
