"""
Tests for timeline_export.py.
No Qt, no GPU, no whisper binary required.
"""
import sys
import os
from dataclasses import dataclass


# Ensure project root is on the path so timeline_export can import core.logger
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from timeline_export import seconds_to_timecode, build_edl, write_edl


# ---------------------------------------------------------------------------
# Minimal duck-type segment for tests
# ---------------------------------------------------------------------------

@dataclass
class _Seg:
    start: float
    end: float


# ---------------------------------------------------------------------------
# Non-drop timecode
# ---------------------------------------------------------------------------

class TestSecondsToTimecodeNonDrop:
    def test_zero(self):
        assert seconds_to_timecode(0.0, 30) == "00:00:00:00"

    def test_one_second_30fps(self):
        assert seconds_to_timecode(1.0, 30) == "00:00:01:00"

    def test_half_second_30fps(self):
        assert seconds_to_timecode(1.5, 30) == "00:00:01:15"

    def test_over_one_hour_30fps(self):
        assert seconds_to_timecode(3661.0, 30) == "01:01:01:00"

    def test_one_second_25fps(self):
        assert seconds_to_timecode(1.0, 25) == "00:00:01:00"

    def test_one_frame_25fps(self):
        assert seconds_to_timecode(0.04, 25) == "00:00:00:01"

    def test_one_second_24fps(self):
        assert seconds_to_timecode(1.0, 24) == "00:00:01:00"

    def test_half_second_60fps(self):
        assert seconds_to_timecode(0.5, 60) == "00:00:00:30"

    def test_separator_is_colon(self):
        tc = seconds_to_timecode(1.0, 30, drop_frame=False)
        assert ";" not in tc
        assert tc.count(":") == 3

    def test_df_flag_ignored_for_25fps(self):
        """drop_frame=True with fps=25 must fall back to non-drop (no semicolon)."""
        tc = seconds_to_timecode(1.0, 25, drop_frame=True)
        assert ";" not in tc
        assert ":" in tc


# ---------------------------------------------------------------------------
# Drop-frame timecode
# ---------------------------------------------------------------------------

class TestSecondsToTimecodeDropFrame:
    def test_zero_df(self):
        assert seconds_to_timecode(0.0, 30, drop_frame=True) == "00:00:00;00"

    def test_10_minutes_df(self):
        # Exactly 10 minutes in 29.97 DF: no frames are dropped at 10-minute marks
        assert seconds_to_timecode(600.0, 30, drop_frame=True) == "00:10:00;00"

    def test_separator_is_semicolon(self):
        tc = seconds_to_timecode(5.0, 30, drop_frame=True)
        assert ";" in tc

    def test_df_unsupported_fps_falls_back(self):
        """fps=25 with drop_frame=True must produce non-drop (colon, not semicolon)."""
        tc = seconds_to_timecode(1.0, 25, drop_frame=True)
        assert ";" not in tc
        assert tc == "00:00:01:00"


# ---------------------------------------------------------------------------
# EDL builder
# ---------------------------------------------------------------------------

class TestBuildEdl:
    def test_empty_returns_empty_string(self):
        assert build_edl([]) == ""

    def test_zero_length_segment_skipped(self):
        assert build_edl([_Seg(start=1.0, end=1.0)]) == ""

    def test_negative_length_segment_skipped(self):
        assert build_edl([_Seg(start=2.0, end=1.0)]) == ""

    def test_header_non_drop(self):
        edl = build_edl([_Seg(0.0, 1.0)], fps=30)
        assert edl.startswith("TITLE:")
        assert "FCM: NON-DROP FRAME" in edl

    def test_header_drop_frame(self):
        edl = build_edl([_Seg(0.0, 1.0)], fps=30, drop_frame=True)
        assert "FCM: DROP FRAME" in edl

    def test_two_segments_numbering(self):
        segs = [_Seg(0.0, 2.0), _Seg(5.0, 8.0)]
        edl = build_edl(segs, fps=30)
        assert "001" in edl
        assert "002" in edl

    def test_source_timecodes_match_segment(self):
        seg = _Seg(start=10.0, end=13.0)
        edl = build_edl([seg], fps=30)
        src_in  = seconds_to_timecode(10.0, 30)
        src_out = seconds_to_timecode(13.0, 30)
        assert src_in in edl
        assert src_out in edl

    def test_record_timecodes_sequential(self):
        """Record in of second event equals duration of first."""
        seg1 = _Seg(start=0.0, end=2.0)   # 2 s → 60 frames at 30fps
        seg2 = _Seg(start=5.0, end=8.0)
        edl = build_edl([seg1, seg2], fps=30)

        rec_in_1  = seconds_to_timecode(0.0, 30)   # 00:00:00:00
        rec_out_1 = seconds_to_timecode(2.0, 30)   # 00:00:02:00 = rec_in_2
        assert rec_in_1 in edl
        assert rec_out_1 in edl
        # rec_out_1 appears as both event-1 rec_out and event-2 rec_in
        assert edl.count(rec_out_1) >= 2

    def test_clip_name_comment(self):
        edl = build_edl([_Seg(0.0, 1.0)], clip_name="myvideo.mp4")
        assert "* FROM CLIP NAME: myvideo.mp4" in edl

    def test_no_clip_name_no_comment(self):
        edl = build_edl([_Seg(0.0, 1.0)], clip_name="")
        assert "FROM CLIP NAME" not in edl

    def test_ends_with_newline(self):
        edl = build_edl([_Seg(0.0, 1.0)])
        assert edl.endswith("\n")

    def test_custom_title(self):
        edl = build_edl([_Seg(0.0, 1.0)], title="My Project")
        assert "TITLE: My Project" in edl


# ---------------------------------------------------------------------------
# write_edl
# ---------------------------------------------------------------------------

class TestWriteEdl:
    def test_writes_file(self, tmp_path):
        out = str(tmp_path / "test.edl")
        write_edl([_Seg(0.0, 2.0)], out, fps=25, title="TestWrite")
        with open(out, encoding="utf-8") as f:
            content = f.read()
        assert "TITLE: TestWrite" in content
        assert "FCM: NON-DROP FRAME" in content
