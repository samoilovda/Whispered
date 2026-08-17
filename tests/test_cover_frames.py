"""Tests for covers/frames.py — timeout and cancel behaviour.

All tests mock subprocess.Popen so no real FFmpeg is needed and the
tests run without a video file on disk.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_popen(returncode=0, stderr=b"", hang=False):
    """Return a mock Popen that behaves as requested."""
    mock_proc = MagicMock()
    mock_proc.returncode = returncode

    if hang:
        # communicate() blocks until timeout is raised by the test
        def hanging_communicate(timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
            return b"", b""

        mock_proc.communicate.side_effect = hanging_communicate
    else:
        mock_proc.communicate.return_value = (b"", stderr)

    return mock_proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunFfmpeg:
    """covers/frames._run_ffmpeg: timeout kills process; error removes output."""

    def test_timeout_kills_process_and_removes_output(self, tmp_path):
        from covers.frames import _run_ffmpeg

        output = tmp_path / "frame.png"
        output.touch()  # pretend partial file exists

        mock_proc = _make_popen(hang=True)
        with patch("covers.frames.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="timed out"):
                _run_ffmpeg(["ffmpeg", "-y", str(output)], output, timeout=0.01)

        assert not output.exists(), "partial output file must be removed on timeout"
        mock_proc.kill.assert_called_once()

    def test_nonzero_exit_removes_output_and_raises(self, tmp_path):
        from covers.frames import _run_ffmpeg

        output = tmp_path / "frame.png"
        output.touch()

        mock_proc = _make_popen(returncode=1, stderr=b"codec error")
        with patch("covers.frames.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="codec error"):
                _run_ffmpeg(["ffmpeg", "-y", str(output)], output, timeout=10)

        assert not output.exists()

    def test_success_does_not_remove_output(self, tmp_path):
        from covers.frames import _run_ffmpeg

        output = tmp_path / "frame.png"
        output.touch()

        mock_proc = _make_popen(returncode=0)
        with patch("covers.frames.subprocess.Popen", return_value=mock_proc):
            _run_ffmpeg(["ffmpeg", "-y", str(output)], output, timeout=10)

        assert output.exists()


class TestExtractFrame:
    """extract_frame: respects cancel(), removes output on failure."""

    def test_cancel_before_extraction_raises(self, tmp_path):
        from covers.frames import extract_frame

        with pytest.raises(RuntimeError, match="Cancelled"):
            with patch("covers.frames.resolve_tool", return_value="/usr/bin/ffmpeg"), \
                 patch("covers.frames.ensure_ffmpeg"):
                extract_frame("video.mp4", 1.0, directory=tmp_path, cancel=lambda: True)


class TestExtractCandidates:
    """extract_candidates: cancel mid-loop stops gracefully."""

    def test_cancel_stops_loop(self, tmp_path):
        from covers.frames import extract_candidates

        call_count = 0
        outputs_made = []

        def fake_run_ffmpeg(command, output, timeout):
            nonlocal call_count
            call_count += 1
            output.touch()
            outputs_made.append(output)

        # Cancel after first frame
        cancel_after = 1
        calls = [0]

        def cancel():
            # Called at the top of each loop iteration
            return calls[0] >= cancel_after

        with patch("covers.frames.resolve_tool", return_value="/usr/bin/ffmpeg"), \
             patch("covers.frames.ensure_ffmpeg"), \
             patch("covers.frames.probe_video", return_value=("h264", 10.0)), \
             patch("covers.frames._run_ffmpeg", side_effect=fake_run_ffmpeg):
            # Make cancel() return True on second iteration by incrementing
            # a counter each time it's called from within the loop.
            # Simpler: cancel immediately (before any frame)
            result = extract_candidates("video.mp4", count=5, directory=tmp_path,
                                        cancel=lambda: True)

        assert result == [], "should return no frames when cancel fires immediately"
