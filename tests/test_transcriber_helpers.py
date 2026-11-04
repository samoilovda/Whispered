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
