"""Tests for utils.py — no Qt, no GPU, no ffmpeg required."""
import pytest
from utils import (
    format_timestamp_srt,
    format_timestamp_vtt,
    format_duration,
    is_supported_format,
    get_thread_count,
)


class TestFormatTimestampSrt:
    def test_zero(self):
        assert format_timestamp_srt(0.0) == "00:00:00,000"

    def test_one_second(self):
        assert format_timestamp_srt(1.0) == "00:00:01,000"

    def test_millis(self):
        assert format_timestamp_srt(1.5) == "00:00:01,500"

    def test_minutes(self):
        assert format_timestamp_srt(90.0) == "00:01:30,000"

    def test_hours(self):
        assert format_timestamp_srt(3661.25) == "01:01:01,250"


class TestFormatTimestampVtt:
    def test_zero(self):
        assert format_timestamp_vtt(0.0) == "00:00:00.000"

    def test_decimal(self):
        assert format_timestamp_vtt(3723.456) == "01:02:03.456"

    def test_separator_dot(self):
        ts = format_timestamp_vtt(1.0)
        assert "." in ts
        assert "," not in ts


class TestFormatDuration:
    def test_under_minute(self):
        assert format_duration(45.0) == "00:45"

    def test_exactly_one_minute(self):
        assert format_duration(60.0) == "01:00"

    def test_hours(self):
        assert format_duration(3661.0) == "01:01:01"

    def test_zero(self):
        assert format_duration(0.0) == "00:00"


class TestIsSupportedFormat:
    def test_audio_formats(self):
        for ext in (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".aac"):
            assert is_supported_format(f"file{ext}"), f"{ext} should be supported"

    def test_video_formats(self):
        for ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
            assert is_supported_format(f"file{ext}"), f"{ext} should be supported"

    def test_uppercase(self):
        assert is_supported_format("AUDIO.MP3")

    def test_unsupported(self):
        assert not is_supported_format("document.pdf")
        assert not is_supported_format("image.png")
        assert not is_supported_format("noextension")

    def test_path_with_dots(self):
        assert is_supported_format("/home/user/my.music.file.mp3")


class TestGetThreadCount:
    def test_balanced_returns_positive(self):
        assert get_thread_count("balanced") >= 1

    def test_efficiency_lte_balanced(self):
        assert get_thread_count("efficiency") <= get_thread_count("balanced")

    def test_balanced_lte_performance(self):
        assert get_thread_count("balanced") <= get_thread_count("performance")

    def test_unknown_mode_returns_positive(self):
        assert get_thread_count("nonexistent_mode") >= 1

    def test_performance_lte_cpu_count(self):
        import os
        cpu = os.cpu_count() or 4
        assert get_thread_count("performance") <= cpu
