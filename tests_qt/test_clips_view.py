"""The Clips tab (N07): add, validate, persist, and render without
blocking — the transcript and the source never change."""

from __future__ import annotations

import pytest

import config
from core.i18n import load_locale, tr
from domain import clips as c
from transcriber import Segment, TranscriptionResult


def _result():
    return TranscriptionResult(segments=[
        Segment(0.0, 6.0, "First part of the talk."), Segment(6.0, 12.0, "Second part."),
    ], language="en", duration=12.0)


@pytest.fixture
def make(monkeypatch, tmp_path, process_events):
    from ui.clips_view import ClipsView

    load_locale("en")
    monkeypatch.setattr(config, "_config", config.Config())
    monkeypatch.setattr("core.paths.artifact_dir", lambda record_id, source: tmp_path / f"a-{record_id}")

    def build(record_id=4):
        v = ClipsView()
        v.set_result(_result())
        v.set_provenance(record_id, "/m/talk.mp4")
        v.show()
        return v

    return build


def _fill(v, title, start, end):
    v.title_edit.setText(title)
    v.start_edit.setText(start)
    v.end_edit.setText(end)


def test_add_persists_and_reloads(make):
    v = make()
    assert tr("clips_empty") in v.status.text() and not v.has_content()
    _fill(v, "Intro", "00:00:01.000", "00:00:05.000")
    v._add()
    assert v.has_content() and v.list.count() == 1
    again = make()
    assert again.list.count() == 1 and "Intro" in again.list.item(0).text()


def test_invalid_ranges_are_refused_with_a_reason(make):
    v = make()
    messages = []
    v.message.connect(lambda text, kind: messages.append(text))
    _fill(v, "", "00:00:01.000", "00:00:05.000")
    v._add()
    _fill(v, "Bad", "00:00:05.000", "00:00:01.000")
    v._add()
    _fill(v, "Past end", "00:00:05.000", "00:00:30.000")
    v._add()
    _fill(v, "Typo", "abc", "00:00:05.000")
    v._add()
    assert not v.has_content()
    assert messages == [tr("clips_problem_empty_title"), tr("clips_problem_bad_range"),
                        tr("clips_problem_beyond_end"), tr("subs_bad_time")]


def test_remove_and_preview(make):
    v = make()
    heard = []
    v.listen_requested.connect(lambda a, b, loop: heard.append((a, b, loop)))
    _fill(v, "Intro", "00:00:01.000", "00:00:05.000")
    v._add()
    _fill(v, "Intro", "00:00:01.000", "00:00:05.000")
    v._preview()
    assert heard and heard[0][0] == 1.0
    v.list.setCurrentRow(0)
    v._remove()
    assert not v.has_content()


def test_a_newer_file_is_left_untouched(make, tmp_path):
    import json

    d = tmp_path / "a-9"
    d.mkdir()
    (d / "clips.json").write_text(json.dumps({"schema_version": 99, "clips": []}))
    v = make(9)
    assert tr("clips_unsupported") in v.status.text()
    assert not v.add_btn.isEnabled()
    assert json.loads((d / "clips.json").read_text())["schema_version"] == 99


def test_edited_timeline_is_labelled(make):
    v = make()
    _fill(v, "Cut", "00:00:01.000", "00:00:05.000")
    v.timeline.setCurrentIndex(v.timeline.findData(c.EDITED))
    v.kept = lambda: [Segment(0.0, 6.0, "x"), Segment(8.0, 12.0, "y")]
    v._add()
    assert tr("clips_timeline_edited") in v.list.item(0).text()


def test_a_half_typed_clip_does_not_follow_the_user_to_another_record(make):
    v = make(4)
    _fill(v, "Mine", "00:00:01.000", "00:00:05.000")
    v.timeline.setCurrentIndex(v.timeline.findData(c.EDITED))
    v.set_provenance(5, "/m/other.mp4")
    assert v.title_edit.text() == "" and v.start_edit.text() == "00:00:00.000"
    assert v.timeline.currentData() == c.SOURCE
    _fill(v, "Mine", "00:00:01.000", "00:00:05.000")
    v.clear()
    assert v.title_edit.text() == "" and v.end_edit.text() == "00:00:00.000"


def test_adding_a_clip_does_not_make_older_clips_look_current(make):
    v = make()
    _fill(v, "Old", "00:00:01.000", "00:00:05.000")
    v._add()
    edited = TranscriptionResult(segments=[Segment(0.0, 12.0, "Totally different words.")],
                                 language="en", duration=12.0)
    v.set_result(edited)                                  # the transcript changed since "Old"
    assert tr("clips_stale") in v.status.text()
    _fill(v, "New", "00:00:06.000", "00:00:10.000")
    v._add()
    again = make()
    again.set_result(edited)
    texts = [again.list.item(i).text() for i in range(again.list.count())]
    assert tr("clips_stale") in texts[0] and tr("clips_stale") not in texts[1]


def test_playhead_buttons_use_the_chosen_timeline(make):
    v = make()
    messages = []
    v.message.connect(lambda text, kind: messages.append(text))
    v.kept = lambda: [Segment(0.0, 4.0, "x"), Segment(8.0, 12.0, "y")]
    v.timeline.setCurrentIndex(v.timeline.findData(c.EDITED))
    v.playhead = lambda: 9.0
    v._take_playhead(v.start_edit)
    assert v.start_edit.text() == "00:00:05.000"          # 4 s kept before + 1 s into the second piece
    v.playhead = lambda: 6.0                               # inside the removed 4–8 s
    v._take_playhead(v.end_edit)
    assert v.end_edit.text() == "00:00:00.000" and messages == [tr("clips_playhead_in_cut")]
    v.timeline.setCurrentIndex(v.timeline.findData(c.SOURCE))
    v._take_playhead(v.end_edit)
    assert v.end_edit.text() == "00:00:06.000"


def test_an_existing_clip_can_be_edited_duplicated_and_restored(make):
    v = make()
    for title, a, b in (("One", "00:00:01.000", "00:00:04.000"), ("Two", "00:00:05.000", "00:00:09.000"),
                        ("Three", "00:00:02.000", "00:00:06.000")):
        _fill(v, title, a, b)
        v._add()
    ids = [clip.id for clip in v._set.clips]
    assert all(ids) and len(set(ids)) == 3
    v.list.setCurrentRow(1)
    assert (v.title_edit.text(), v.start_edit.text()) == ("Two", "00:00:05.000")     # picked → in the form
    v.title_edit.setText("Two, fixed")
    v.end_edit.setText("00:00:10.000")
    v._update_selected()
    assert [c_.title for c_ in v._set.clips] == ["One", "Two, fixed", "Three"]       # same place, others intact
    assert v._set.clips[1].id == ids[1] and v._set.clips[1].end == 10.0
    assert v.list.currentRow() == 1
    v._duplicate()
    assert [c_.title for c_ in v._set.clips][1:3] == ["Two, fixed", tr("clips_copy_title", title="Two, fixed")]
    assert v._set.clips[2].id not in ids
    v.list.setCurrentRow(0)
    v._remove()
    assert [c_.title for c_ in v._set.clips][0] == "Two, fixed"
    v._undo_remove()
    assert [c_.title for c_ in v._set.clips][0] == "One" and v._set.clips[0].id == ids[0]
    again = make()                                                                    # survives a restart
    assert [c_.title for c_ in again._set.clips] == [c_.title for c_ in v._set.clips]
    assert [c_.id for c_ in again._set.clips] == [c_.id for c_ in v._set.clips]


def test_an_invalid_change_leaves_the_clip_alone(make):
    v = make()
    _fill(v, "One", "00:00:01.000", "00:00:04.000")
    v._add()
    v.list.setCurrentRow(0)
    messages = []
    v.message.connect(lambda text, kind: messages.append(text))
    v.end_edit.setText("00:00:00.500")
    v._update_selected()
    assert v._set.clips[0].end == 4.0 and messages == [tr("clips_problem_bad_range")]
