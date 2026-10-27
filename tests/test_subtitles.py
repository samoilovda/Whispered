"""domain.subtitles (N02): cutting to limits, validation, edits, files."""

from __future__ import annotations

from domain import subtitles as s
from domain.transcription import Segment

P = s.SubtitlePreset()


def _codes(cues, preset=P, duration=None):
    return [(i.index, i.code) for i in s.validate(cues, preset, duration)]


def test_a_long_segment_is_cut_into_readable_cues_that_share_its_time():
    text = "This is a rather long sentence that has to be shown on several lines of subtitles properly"
    cues = s.cues_from_segments([Segment(10.0, 20.0, text)])
    assert len(cues) >= 2
    assert cues[0].start == 10.0 and cues[-1].end == 20.0
    assert all(a.end == b.start for a, b in zip(cues, cues[1:]))
    assert all(len(line) <= P.max_chars_per_line and len(c.lines) <= P.max_lines
               for c in cues for line in c.lines)
    assert " ".join(c.text.replace("\n", " ") for c in cues) == text


def test_cyrillic_and_unicode_are_cut_the_same_way():
    cues = s.cues_from_segments([Segment(0.0, 6.0, "Привет, это длинное предложение для проверки переноса строк в субтитрах — ёжик 🙂")])
    assert cues and all(len(line) <= P.max_chars_per_line for c in cues for line in c.lines)
    assert cues[-1].text.endswith("🙂") and len(cues[-1].text) > 1   # no one-word widow


def test_a_word_longer_than_a_line_stays_whole_and_is_flagged():
    long_word = "x" * 60
    cues = s.cues_from_segments([Segment(0.0, 3.0, f"a {long_word} b")])
    assert any(long_word in c.text for c in cues)
    assert (0, s.LINE_TOO_LONG) in _codes(cues) or any(i.code == s.LINE_TOO_LONG for i in s.validate(cues))


def test_every_kind_of_problem_is_reported():
    cues = [
        s.SubtitleCue(0.0, 0.0, "zero"),                     # bad time
        s.SubtitleCue(5.0, 4.0, "negative"),                 # end before start
        s.SubtitleCue(10.0, 10.2, "short"),                  # too short
        s.SubtitleCue(10.1, 12.0, "overlaps the one before"),
        s.SubtitleCue(20.0, 30.0, "far too long on screen"),
        s.SubtitleCue(31.0, 32.0, "this line is much too long to read in a single second of screen"),
        s.SubtitleCue(33.0, 40.0, "one\ntwo\nthree"),
        s.SubtitleCue(41.0, 43.0, "   "),
        s.SubtitleCue(500.0, 502.0, "past the end"),
    ]
    codes = {code for _, code in _codes(cues, duration=100.0)}
    assert codes >= {s.BAD_TIME, s.TOO_SHORT, s.OVERLAP, s.TOO_LONG, s.CPS_HIGH,
                     s.LINE_TOO_LONG, s.TOO_MANY_LINES, s.EMPTY, s.BEYOND_END}
    assert {i.code for i in s.blocking(s.validate(cues, P, 100.0))} == {s.BAD_TIME, s.EMPTY}
    assert s.validate([s.SubtitleCue(0, 2, "fine and calm")], P, 10.0) == []


def test_split_and_merge_never_touch_the_text_and_keep_time_continuous():
    cue = s.SubtitleCue(0.0, 6.0, "first half of it\nsecond half of it")
    parts = s.split_cue([cue], 0, 16)
    assert [c.text for c in parts] == ["first half of it", "second half of it"]
    assert parts[0].start == 0.0 and parts[0].end == parts[1].start and parts[1].end == 6.0
    merged = s.merge_cues(parts, 0)
    assert len(merged) == 1 and merged[0].start == 0.0 and merged[0].end == 6.0
    assert merged[0].text.replace("\n", " ") == "first half of it second half of it"
    assert s.split_cue([s.SubtitleCue(0, 1, "oneword")], 0, 3) == [s.SubtitleCue(0, 1, "oneword")]
    assert s.merge_cues(parts, 5) == parts


def test_srt_and_vtt_round_trip_with_the_same_times_and_text():
    cues = [s.SubtitleCue(0.5, 2.25, "Привет, мир\nвторая строка"),
            s.SubtitleCue(3725.001, 3727.9, "Tom & Jerry <b>not markup</b>")]
    for text in (s.to_srt(cues), s.to_vtt(cues)):
        back = s.parse_cues(text)
        assert [(c.start, c.end, c.text) for c in back] == [(c.start, c.end, c.text) for c in cues]
    assert s.to_srt(cues).splitlines()[1] == "00:00:00,500 --> 00:00:02,250"
    assert "01:02:05.001 --> 01:02:07.900" in s.to_vtt(cues)
    assert s.to_vtt(cues).startswith("WEBVTT\n")
    assert "&lt;b&gt;" in s.to_vtt(cues)


def test_text_cannot_break_the_file_structure():
    cue = s.SubtitleCue(0, 2, "before\n\n00:00:09,000 --> 00:00:10,000\nafter")
    assert len(s.parse_cues(s.to_srt([cue]))) == 1          # blank line and arrow neutralised
    assert "-->" not in s.to_srt([cue]).split("\n", 2)[2]


def test_track_round_trip_and_newer_schema():
    track = s.SubtitleTrack([s.SubtitleCue(0, 1, "a")], s.SubtitlePreset(max_lines=1), "rev")
    again = s.SubtitleTrack.from_dict(track.to_dict())
    assert again.cues == track.cues and again.preset.max_lines == 1 and again.transcript_revision == "rev"
    assert s.SubtitleTrack.from_dict({"schema_version": 99}) is None
    assert s.SubtitlePreset.from_dict({"max_lines": 99, "max_cps": "x"}) == s.SubtitlePreset()


def test_reading_a_subtitle_file_for_import(tmp_path):
    import pytest

    from application import subtitle_store as store

    srt = tmp_path / "a.srt"
    srt.write_bytes("﻿1\n00:00:05,000 --> 00:00:07,000\nSecond\nline\n\n2\n00:00:00,500 --> 00:00:02,000\nFirst &amp; fine\n".encode())
    cues = store.read_subtitle_file(str(srt))
    assert [c.text for c in cues] == ["First & fine", "Second\nline"]       # ordered, multiline kept
    assert cues[0].start == 0.5
    vtt = tmp_path / "a.vtt"
    vtt.write_text("WEBVTT\n\n00:01.000 --> 00:02.000\nHello\n", encoding="utf-8")
    assert store.read_subtitle_file(str(vtt))[0].start == 1.0
    for name, data, why in (("empty.srt", b"nothing here", "no_cues"), ("bin.srt", b"\xff\xfe\x00bad", "not_utf8"),
                            ("big.srt", b"x" * (store.MAX_IMPORT_BYTES + 1), "too_large")):
        path = tmp_path / name
        path.write_bytes(data)
        with pytest.raises(store.SubtitleImportError, match=why):
            store.read_subtitle_file(str(path))
    a, b = [s.SubtitleCue(0, 1, "a")], [s.SubtitleCue(0.5, 1.5, "b")]
    assert store.combine_cues(a, b, "replace") == b
    assert [c.text for c in store.combine_cues(b, a, "add")] == ["a", "b"]
    assert s.SubtitleTrack.from_dict(s.SubtitleTrack(a, origin="x.srt").to_dict()).origin == "x.srt"
