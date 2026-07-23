"""Regression tests for small transcription helpers without invoking Whisper."""

import os
import sys
from unittest.mock import patch

# Other isolated test modules install a lightweight transcriber stub.  This
# regression test intentionally exercises the real helper.
sys.modules.pop("transcriber", None)
from transcriber import _convert_to_wav


class _CompletedProcess:
    returncode = 1


def test_convert_to_wav_uses_unique_temporary_paths_and_cleans_failed_output(tmp_path):
    """Two conversions of same basename must never share a predictable WAV."""
    outputs = []

    def failed_run(command, **kwargs):
        outputs.append(command[-1])
        return _CompletedProcess()

    with patch("core.external_tools.resolve_tool", return_value="ffmpeg"), \
         patch("transcriber.subprocess.run", side_effect=failed_run):
        first = _convert_to_wav(str(tmp_path / "same-name.mp4"))
        second = _convert_to_wav(str(tmp_path / "same-name.mp4"))

    assert first is None
    assert second is None
    assert outputs[0] != outputs[1]
    assert all(not os.path.exists(output) for output in outputs)
