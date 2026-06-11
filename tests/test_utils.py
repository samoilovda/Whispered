"""Tests for utils timestamp/format/extension helpers."""

from utils import (
    is_supported_format,
    get_file_extension,
    format_duration,
    format_timestamp_srt,
    format_timestamp_vtt,
)


def test_is_supported_format():
    assert is_supported_format("a.mp3")
    assert is_supported_format("/path/to/Video.MP4")  # case-insensitive
    assert not is_supported_format("notes.txt")
    assert not is_supported_format("noext")


def test_get_file_extension():
    assert get_file_extension("A.MKV") == ".mkv"
    assert get_file_extension("file") == ""


def test_format_duration_under_hour():
    assert format_duration(0) == "00:00"
    assert format_duration(65) == "01:05"


def test_format_duration_over_hour():
    assert format_duration(3661) == "01:01:01"


def test_format_timestamp_srt():
    assert format_timestamp_srt(0) == "00:00:00,000"
    # 1h 1m 1s 500ms
    assert format_timestamp_srt(3661.5) == "01:01:01,500"


def test_format_timestamp_vtt():
    assert format_timestamp_vtt(0) == "00:00:00.000"
    assert format_timestamp_vtt(3661.5) == "01:01:01.500"
