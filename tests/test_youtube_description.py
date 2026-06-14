"""Unit tests for core/youtube_description.py — no Qt required."""

import sys
import types

# Stub Qt and related modules so core/__init__.py doesn't break on import
for _mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui",
             "PyQt6.QtMultimedia"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

_qtcore = sys.modules["PyQt6.QtCore"]
_qtcore.QThread = type("QThread", (), {"start": lambda *a: None, "isRunning": lambda *a: False})
_qtcore.pyqtSignal = lambda *a, **kw: None
_qtcore.QObject = type("QObject", (), {"__init__": lambda *a, **kw: None})

_lm_stub = types.ModuleType("core.lm_client")
_lm_stub.LMStudioClient = object
_lm_stub.DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"
sys.modules.setdefault("core.lm_client", _lm_stub)

_ai_stub = types.ModuleType("core.ai_worker")
_ai_stub.AIProcessingWorker = object
sys.modules.setdefault("core.ai_worker", _ai_stub)

import pytest
from core.youtube_description import format_youtube_timestamp, format_youtube_description


# ── format_youtube_timestamp ─────────────────────────────────────────────────

class TestFormatYoutubeTimestamp:
    def test_zero(self):
        assert format_youtube_timestamp(0) == "0:00"

    def test_154_seconds(self):
        assert format_youtube_timestamp(154) == "2:34"

    def test_725_seconds(self):
        assert format_youtube_timestamp(725) == "12:05"

    def test_3600_seconds(self):
        assert format_youtube_timestamp(3600) == "1:00:00"

    def test_3723_seconds(self):
        assert format_youtube_timestamp(3723) == "1:02:03"

    def test_no_leading_zero_on_minutes(self):
        # YouTube format: "2:34" not "02:34"
        result = format_youtube_timestamp(154)
        assert not result.startswith("0")

    def test_seconds_always_two_digits(self):
        assert format_youtube_timestamp(65) == "1:05"


# ── format_youtube_description ───────────────────────────────────────────────

class TestFormatYoutubeDescription:
    def test_empty_input(self):
        assert format_youtube_description([]) == ""

    def test_all_empty_titles_returns_empty(self):
        chapters = [{"start": 0, "title": ""}, {"start": 10, "title": "  "}]
        assert format_youtube_description(chapters) == ""

    def test_first_entry_forced_to_zero(self):
        chapters = [{"start": 30, "title": "Intro"}, {"start": 90, "title": "Body"}]
        result = format_youtube_description(chapters)
        assert result.startswith("0:00 Intro")

    def test_sorted_ascending(self):
        chapters = [
            {"start": 90, "title": "Body"},
            {"start": 0, "title": "Intro"},
            {"start": 180, "title": "Outro"},
        ]
        lines = format_youtube_description(chapters).splitlines()
        assert lines[0].startswith("0:00")
        assert lines[1].startswith("1:30")
        assert lines[2].startswith("3:00")

    def test_skip_blank_titles(self):
        chapters = [
            {"start": 0, "title": "Intro"},
            {"start": 60, "title": ""},
            {"start": 120, "title": "End"},
        ]
        result = format_youtube_description(chapters)
        lines = result.splitlines()
        assert len(lines) == 2
        assert "Intro" in lines[0]
        assert "End" in lines[1]

    def test_skip_duplicate_timestamps(self):
        chapters = [
            {"start": 0, "title": "Intro"},
            {"start": 0, "title": "Duplicate"},
            {"start": 60, "title": "Body"},
        ]
        lines = format_youtube_description(chapters).splitlines()
        assert len(lines) == 2

    def test_skip_duplicate_timestamps_after_sort(self):
        # Both entries have start=60; second must be dropped (not strictly greater)
        chapters = [
            {"start": 0, "title": "Intro"},
            {"start": 60, "title": "Body"},
            {"start": 60, "title": "Also Body"},  # same time as previous
        ]
        lines = format_youtube_description(chapters).splitlines()
        assert len(lines) == 2
        assert "Also Body" not in format_youtube_description(chapters)

    def test_newline_separated(self):
        chapters = [
            {"start": 0, "title": "A"},
            {"start": 60, "title": "B"},
            {"start": 120, "title": "C"},
        ]
        result = format_youtube_description(chapters)
        assert result == "0:00 A\n1:00 B\n2:00 C"

    def test_float_start_coerced(self):
        chapters = [{"start": 60.9, "title": "Body"}]
        result = format_youtube_description(chapters)
        assert result.startswith("0:00")  # forced to 0

    def test_string_start_coerced(self):
        chapters = [{"start": "45", "title": "Mid"}]
        result = format_youtube_description(chapters)
        assert "Mid" in result

    def test_invalid_start_skipped(self):
        chapters = [
            {"start": "bad", "title": "Skip me"},
            {"start": 0, "title": "Keep"},
        ]
        result = format_youtube_description(chapters)
        assert "Skip me" not in result
        assert "Keep" in result
