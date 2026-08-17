"""Tests for video_cut._run's cancellation and timeout handling.

Exercises real subprocesses (no mocking) via a portable Python child
process standing in for ffmpeg, so the polling/terminate/kill logic is
verified end to end rather than against a mock.
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import video_cut
from video_cut import VideoCutCancelled, VideoCutError, _run, _write_concat_list


def _py(code: str) -> list:
    return [sys.executable, "-c", code]


class TestRunWithoutCancel:
    """should_cancel=None must behave exactly like the old subprocess.run path."""

    def test_success(self):
        _run(_py("pass"))  # must not raise

    def test_failure_raises_video_cut_error(self):
        with pytest.raises(VideoCutError, match="exited 1"):
            _run(_py("import sys; sys.exit(1)"))


class TestRunWithCancel:
    def test_success_path_still_works(self):
        _run(_py("pass"), should_cancel=lambda: False)

    def test_failure_path_still_raises(self):
        with pytest.raises(VideoCutError, match="exited 1"):
            _run(_py("import sys; sys.exit(1)"), should_cancel=lambda: False)

    def test_cancel_terminates_long_running_process_promptly(self):
        started = time.monotonic()
        with pytest.raises(VideoCutCancelled):
            _run(_py("import time; time.sleep(30)"), should_cancel=lambda: True)
        # Detected within one poll interval and killed well under the
        # 30s the child would otherwise sleep for.
        assert time.monotonic() - started < 5

    def test_cancel_flips_mid_run(self, monkeypatch):
        """should_cancel starts False and flips True after the process is
        already running — must still be caught and terminated."""
        calls = {"n": 0}

        def should_cancel():
            calls["n"] += 1
            return calls["n"] > 2

        started = time.monotonic()
        with pytest.raises(VideoCutCancelled):
            _run(_py("import time; time.sleep(30)"), should_cancel=should_cancel)
        assert time.monotonic() - started < 5


class TestRunTimeout:
    def test_exceeding_max_run_seconds_raises_and_kills(self, monkeypatch):
        monkeypatch.setattr(video_cut, "_MAX_RUN_SECONDS", 0)
        with pytest.raises(VideoCutError, match="timed out"):
            _run(_py("import time; time.sleep(30)"), should_cancel=lambda: False)


class TestConcatListPaths:
    def test_apostrophe_is_preserved_with_ffconcat_escaping(self, tmp_path):
        source = tmp_path / "O'Brien.mp4"
        list_path = _write_concat_list(
            str(source), [SimpleNamespace(start=1.0, end=2.0)], str(tmp_path)
        )

        content = Path(list_path).read_text(encoding="utf-8")
        assert "O'\\''Brien.mp4" in content

    def test_newline_is_rejected_instead_of_targeting_the_wrong_file(self, tmp_path):
        source = tmp_path / "line\nbreak.mp4"
        with pytest.raises(VideoCutError, match="newlines"):
            _write_concat_list(
                str(source), [SimpleNamespace(start=1.0, end=2.0)], str(tmp_path)
            )
