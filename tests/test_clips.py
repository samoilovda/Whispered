"""domain.clips (N07): timelines, checks and clip subtitles."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from domain import clips as c
from domain.subtitles import SubtitleCue


def seg(a, b):
    return SimpleNamespace(start=a, end=b)


KEPT = [seg(0, 10), seg(20, 30), seg(40, 60)]       # source 10–20 and 30–40 were cut out


def test_source_and_edited_timelines_are_told_apart():
    source = c.ClipDraft("a", 22, 28)
    assert c.source_pieces(source, KEPT) == [(22, 28)]
    edited = c.ClipDraft("a", 12, 18, c.EDITED)           # inside the 2nd kept segment (edited 10–20)
    assert c.source_pieces(edited, KEPT) == [(22.0, 28.0)]


def test_an_edited_range_over_a_cut_is_several_source_pieces():
    assert c.edited_range_to_source(KEPT, 8, 12) == [(8.0, 10.0), (20.0, 22.0)]
    assert c.edited_range_to_source(KEPT, 18, 25) == [(28.0, 30.0), (40.0, 45.0)]
    assert c.edited_range_to_source(KEPT, 0, 100) == [(0.0, 10.0), (20.0, 30.0), (40.0, 60.0)]
    assert c.edited_range_to_source(KEPT, 5, 5) == [] and c.edited_range_to_source([], 0, 5) == []
    assert c.edited_duration(KEPT) == 40


def test_checks_name_what_stops_a_render():
    ok = c.ClipDraft("t", 0, 30)
    assert c.check_clip(ok, media_duration=100) == []
    assert c.CLIP_EMPTY_TITLE in c.check_clip(c.ClipDraft(" ", 0, 30))
    assert c.check_clip(c.ClipDraft("t", 5, 5)) == [c.CLIP_BAD_RANGE]
    assert c.check_clip(c.ClipDraft("t", -1, 5)) == [c.CLIP_BAD_RANGE]
    assert c.CLIP_TOO_SHORT in c.check_clip(c.ClipDraft("t", 0, 0.5))
    assert c.CLIP_TOO_LONG in c.check_clip(c.ClipDraft("t", 0, 700))
    assert c.CLIP_BEYOND_END in c.check_clip(c.ClipDraft("t", 90, 130), media_duration=100)
    assert c.CLIP_BEYOND_END in c.check_clip(c.ClipDraft("t", 30, 50, c.EDITED), edited_duration=40)
    assert c.check_clip(c.ClipDraft("t", 30, 50, c.EDITED), media_duration=10, edited_duration=60) == []


def test_clip_subtitles_start_at_zero_and_follow_the_pieces():
    cues = [SubtitleCue(5, 9, "before"), SubtitleCue(21, 24, "inside"),
            SubtitleCue(27, 33, "straddles the end"), SubtitleCue(50, 52, "far")]
    one = c.cues_for_clip(cues, [(20.0, 30.0)])
    assert [(x.start, x.end, x.text) for x in one] == [(1.0, 4.0, "inside"), (7.0, 10.0, "straddles the end")]
    two = c.cues_for_clip(cues, [(8.0, 10.0), (20.0, 22.0)])
    assert [(x.start, x.end, x.text) for x in two] == [(0.0, 1.0, "before"), (3.0, 4.0, "inside")]
    assert c.cues_for_clip(cues, []) == []
    assert c.cues_for_clip([SubtitleCue(10, 10.01, "blink")], [(0.0, 20.0)]) == []   # too small to show


def test_set_round_trip_and_newer_schema():
    s = c.ClipSet([c.ClipDraft("A", 1, 9), c.ClipDraft("B", 2, 8, c.EDITED)], "rev")
    again = c.ClipSet.from_dict(s.to_dict())
    assert again.clips == s.clips and again.transcript_revision == "rev"
    assert c.ClipSet.from_dict({"schema_version": 99}) is None
    assert c.ClipSet.from_dict({"clips": [{"title": "x"}, {"title": "ok", "start": 0, "end": 5}]}).clips == [
        c.ClipDraft("ok", 0.0, 5.0)]
    assert c.safe_file_stem('A/B: "quote"?') == "A_B_ _quote" and c.safe_file_stem("???") == "clip"


# ------------------------------------------------------------ rendering plan

def test_plan_checks_before_anything_is_rendered(tmp_path):
    from application import clips as app

    source = tmp_path / "talk.mp4"
    source.write_bytes(b"x")
    ok = c.ClipDraft("Intro", 0, 20)
    plan = app.plan_render(ok, source=str(source), output=str(tmp_path / "intro.mp4"), kept=[],
                           cues=[SubtitleCue(1, 3, "hello")], media_duration=60)
    assert plan.pieces == [(0, 20)] and "hello" in plan.subtitles
    assert plan.srt_path == str(tmp_path / "intro.srt")
    for clip, kwargs, reason in (
        (c.ClipDraft("", 0, 20), {}, c.CLIP_EMPTY_TITLE),
        (c.ClipDraft("x", 50, 90), {}, c.CLIP_BEYOND_END),
    ):
        try:
            app.plan_render(clip, source=str(source), output=str(tmp_path / "o.mp4"), kept=[],
                            cues=[], media_duration=60, **kwargs)
        except ValueError as exc:
            assert str(exc) == reason
        else:
            raise AssertionError(reason)
    for output, reason in ((str(source), "same_as_source"), (str(tmp_path / "o.mov"), "not_mp4")):
        try:
            app.plan_render(ok, source=str(source), output=output, kept=[], cues=[], media_duration=60)
        except ValueError as exc:
            assert str(exc) == reason
    try:
        app.plan_render(ok, source=str(tmp_path / "gone.mp4"), output=str(tmp_path / "o.mp4"),
                        kept=[], cues=[], media_duration=60)
    except ValueError as exc:
        assert str(exc) == "source_missing"


def test_an_edited_timeline_clip_plans_the_pieces_it_spans(tmp_path):
    from application import clips as app

    source = tmp_path / "talk.mp4"
    source.write_bytes(b"x")
    plan = app.plan_render(c.ClipDraft("Across a cut", 8, 12, c.EDITED), source=str(source),
                           output=str(tmp_path / "x.mp4"), kept=KEPT,
                           cues=[SubtitleCue(9, 9.5, "before cut"), SubtitleCue(20.5, 21.5, "after cut")],
                           media_duration=60)
    assert plan.pieces == [(8.0, 10.0), (20.0, 22.0)]
    assert plan.subtitles.count("-->") == 2 and "00:00:02,500 --> 00:00:03,500" in plan.subtitles


def test_render_writes_subtitles_after_the_video_and_nothing_on_failure(tmp_path, monkeypatch):
    from application import clips as app
    import video_cut

    plan = app.RenderPlan("/s.mp4", str(tmp_path / "o.mp4"), [(0.0, 5.0)], "1\n...\n", str(tmp_path / "o.srt"))
    seen = {}

    def fake_assemble(source, segments, output, **kw):
        seen["pieces"] = [(p.start, p.end) for p in segments]
        Path(output).write_bytes(b"video")

    from pathlib import Path
    monkeypatch.setattr(video_cut, "assemble_draft", fake_assemble)
    app.render(plan)
    assert seen["pieces"] == [(0.0, 5.0)] and (tmp_path / "o.srt").exists()

    (tmp_path / "o.srt").unlink()
    (tmp_path / "o.mp4").unlink()

    def cancelled(*a, **k):
        raise video_cut.VideoCutCancelled("cancelled")

    monkeypatch.setattr(video_cut, "assemble_draft", cancelled)
    try:
        app.render(plan)
    except video_cut.VideoCutCancelled:
        pass
    assert not (tmp_path / "o.mp4").exists() and not (tmp_path / "o.srt").exists()


def test_store_round_trip_and_newer_schema(tmp_path):
    from application import clips as app
    from application.user_edits import save_overlay

    app.save_clips(tmp_path, c.ClipSet([c.ClipDraft("A", 1, 9)], "r"))
    assert app.load_clips(tmp_path).clips == [c.ClipDraft("A", 1, 9)]
    app.save_clips(tmp_path, c.ClipSet())
    assert not app.clips_file(tmp_path).exists() and app.load_clips(tmp_path).clips == []
    save_overlay(app.clips_file(tmp_path), {"schema_version": 99, "clips": []})
    assert app.load_clips(tmp_path) is None


def test_a_failed_subtitle_write_keeps_the_previous_video(tmp_path, monkeypatch):
    from application import clips as app
    import video_cut

    (tmp_path / "o.mp4").write_bytes(b"ORIGINAL")
    (tmp_path / "o.srt").mkdir()                       # the subtitle path is unwritable
    plan = app.RenderPlan("/s.mp4", str(tmp_path / "o.mp4"), [(0.0, 5.0)], "1\n...\n", str(tmp_path / "o.srt"))

    def fake_assemble(source, segments, output, **kw):
        Path(output).write_bytes(b"NEW")

    from pathlib import Path
    monkeypatch.setattr(video_cut, "assemble_draft", fake_assemble)
    with pytest.raises(OSError):
        app.render(plan)
    assert (tmp_path / "o.mp4").read_bytes() == b"ORIGINAL"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["o.mp4", "o.srt"]

    (tmp_path / "o.srt").rmdir()                       # a cancel also keeps the old pair
    (tmp_path / "o.srt").write_text("OLD")

    def cancelled(*a, **k):
        Path(a[2]).write_bytes(b"PARTIAL")
        raise video_cut.VideoCutCancelled("cancelled")

    monkeypatch.setattr(video_cut, "assemble_draft", cancelled)
    with pytest.raises(video_cut.VideoCutCancelled):
        app.render(plan)
    assert (tmp_path / "o.mp4").read_bytes() == b"ORIGINAL" and (tmp_path / "o.srt").read_text() == "OLD"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["o.mp4", "o.srt"]

    monkeypatch.setattr(video_cut, "assemble_draft", fake_assemble)   # success replaces both
    app.render(plan)
    assert (tmp_path / "o.mp4").read_bytes() == b"NEW" and (tmp_path / "o.srt").read_text() == "1\n...\n"


def test_source_to_edited_converts_before_after_and_inside_a_cut():
    kept = [seg(0, 10), seg(20, 30)]
    assert c.source_to_edited(kept, 5) == 5
    assert c.source_to_edited(kept, 25) == 15
    assert c.source_to_edited(kept, 10) == 10 and c.source_to_edited(kept, 20) == 10
    assert c.source_to_edited(kept, 15) is None and c.source_to_edited(kept, 31) is None
