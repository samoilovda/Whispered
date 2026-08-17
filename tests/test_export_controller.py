"""Unit tests for application/export_controller.py — the Qt-free "what to
export and whether it worked" logic extracted out of
ui/main_window.py::_export_result (see
docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R6-cont).
"""

from __future__ import annotations

from unittest.mock import patch

from application.export_controller import (
    export_many_to_directory,
    export_single,
    format_extension,
)
from domain.transcription import Segment, TranscriptionResult


def _result() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[Segment(start=0.0, end=1.0, text="hello world")],
        language="en",
        duration=1.0,
    )


def test_format_extension_shares_txt_between_txt_and_txt_ts():
    assert format_extension("txt") == "txt"
    assert format_extension("txt_ts") == "txt"
    assert format_extension("srt") == "srt"
    assert format_extension("json") == "json"


def test_export_single_writes_the_requested_format(tmp_path):
    filepath = str(tmp_path / "out.txt")
    export_single(_result(), filepath, "txt")
    assert "hello world" in open(filepath, encoding="utf-8").read()


def test_export_single_propagates_the_exception():
    """The single-file path shows the caller the actual error — must not
    swallow it, unlike the multi-format path."""
    with patch("application.export_controller.export_result", side_effect=ValueError("boom")):
        try:
            export_single(_result(), "/dev/null/impossible.txt", "txt")
        except ValueError as exc:
            assert str(exc) == "boom"
        else:
            raise AssertionError("expected ValueError to propagate")


def test_export_many_to_directory_writes_one_file_per_format(tmp_path):
    outcome = export_many_to_directory(
        _result(), str(tmp_path), ["txt", "srt", "json"], default_name="rec"
    )
    assert outcome.succeeded == ["txt", "srt", "json"]
    assert outcome.failed == []
    assert outcome.any_succeeded and not outcome.any_failed
    assert (tmp_path / "rec.txt").exists()
    assert (tmp_path / "rec.srt").exists()
    assert (tmp_path / "rec.json").exists()


def test_export_many_to_directory_txt_ts_gets_a_distinct_filename(tmp_path):
    outcome = export_many_to_directory(
        _result(), str(tmp_path), ["txt", "txt_ts"], default_name="rec"
    )
    assert outcome.succeeded == ["txt", "txt_ts"]
    assert (tmp_path / "rec.txt").exists()
    assert (tmp_path / "rec_ts.txt").exists()


def test_export_many_to_directory_one_failure_does_not_stop_the_rest(tmp_path):
    """A failure in one format must not prevent the others from being
    written — this is the whole point of reporting an ExportOutcome
    instead of raising on the first error."""
    real_export = __import__("application.export_controller", fromlist=["export_result"]).export_result

    def flaky(result, filepath, format_key):
        if format_key == "srt":
            raise RuntimeError("disk full")
        return real_export(result, filepath, format_key)

    with patch("application.export_controller.export_result", side_effect=flaky):
        outcome = export_many_to_directory(
            _result(), str(tmp_path), ["txt", "srt", "json"], default_name="rec"
        )

    assert outcome.succeeded == ["txt", "json"]
    assert outcome.failed == ["srt"]
    assert (tmp_path / "rec.txt").exists()
    assert (tmp_path / "rec.json").exists()
    assert not (tmp_path / "rec.srt").exists()
