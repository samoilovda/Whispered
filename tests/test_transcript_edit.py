"""domain.transcript_edit — the editor buffer as data (UX-05 / T05)."""

from __future__ import annotations

from domain.transcript_edit import (
    ERROR_EMPTY, ERROR_MALFORMED_TIMESTAMP, ERROR_OUT_OF_ORDER, ERROR_TEXT_BEFORE_FIRST,
    format_edit_timestamp, parse_edit_buffer, render_edit_buffer, same_segments,
)
from domain.transcription import Segment, Word


def _segments():
    return [
        Segment(0.0, 4.5, "first line", speaker="SPEAKER_00",
                words=[Word(0.0, 1.0, "first")], confidence=0.4),
        Segment(4.5, 9.0, "second line", speaker="SPEAKER_01", confidence=0.9),
    ]


def test_round_trip_is_a_no_op():
    original = _segments()
    parsed = parse_edit_buffer(render_edit_buffer(original), original, 9.0)
    assert parsed.ok
    assert same_segments(original, parsed.segments)
    assert parsed.segments[0].words == original[0].words
    assert parsed.segments[0].confidence == 0.4  # unchanged text keeps it


def test_text_edit_keeps_timing_and_drops_confidence():
    original = _segments()
    buffer = render_edit_buffer(original).replace("first line", "corrected line")
    parsed = parse_edit_buffer(buffer, original, 9.0)
    assert parsed.segments[0].text == "corrected line"
    assert parsed.segments[0].end == 4.5
    assert parsed.segments[0].confidence is None


def test_sub_millisecond_start_still_matches_its_segment():
    original = [Segment(12.3456, 15.0, "x", words=[Word(12.3456, 13.0, "x")])]
    parsed = parse_edit_buffer(render_edit_buffer(original), original, 15.0)
    assert parsed.segments[0].start == 12.3456
    assert parsed.segments[0].words


def test_malformed_timestamp_is_an_error_not_continuation_text():
    original = _segments()
    buffer = "[00:00:00.000] [SPEAKER_00] first line\n[00:00:4.500] second line"
    parsed = parse_edit_buffer(buffer, original, 9.0)
    assert not parsed.ok
    assert (parsed.error, parsed.line) == (ERROR_MALFORMED_TIMESTAMP, 2)


def test_out_of_order_and_empty_and_leading_text():
    original = _segments()
    out_of_order = "[00:00:05.000] b\n[00:00:01.000] a"
    assert parse_edit_buffer(out_of_order, original, 9.0).error == ERROR_OUT_OF_ORDER
    assert parse_edit_buffer("   \n", original, 9.0).error == ERROR_EMPTY
    leading = "stray\n[00:00:00.000] a"
    assert parse_edit_buffer(leading, original, 9.0).error == ERROR_TEXT_BEFORE_FIRST


def test_continuation_lines_join_and_split_lines_get_explicit_times():
    original = _segments()
    buffer = (
        "[00:00:00.000] [SPEAKER_00] first\nline\n"
        "[00:00:02.000] [SPEAKER_00] split off\n"
        "[00:00:04.500] [SPEAKER_01] second line"
    )
    parsed = parse_edit_buffer(buffer, original, 9.0)
    assert [s.text for s in parsed.segments] == ["first line", "split off", "second line"]
    assert parsed.segments[1].start == 2.0 and parsed.segments[1].end == 4.5


def test_format_edit_timestamp():
    assert format_edit_timestamp(3725.5) == "01:02:05.500"
