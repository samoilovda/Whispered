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
