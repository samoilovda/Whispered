"""The Subtitles tab (N02): generate, edit, check, export — and the
transcript and the source never change."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

import config
from core.i18n import load_locale, tr
from transcriber import Segment, TranscriptionResult


def _result():
    return TranscriptionResult(segments=[
        Segment(0.0, 6.0, "This is a long enough sentence to need two subtitle lines on screen."),
        Segment(6.0, 8.0, "Short one."),
    ], language="en", duration=8.0)


@pytest.fixture
def view(monkeypatch, tmp_path, process_events):
    from ui.subtitle_view import SubtitleView

    load_locale("en")
    monkeypatch.setattr(config, "_config", config.Config())
    monkeypatch.setattr("core.paths.artifact_dir", lambda record_id, source: tmp_path / f"a-{record_id}")
    result = _result()
    original = [(s.start, s.end, s.text) for s in result.segments]

    def make(record_id=3):
        v = SubtitleView()
        v.set_result(result)
        v.set_provenance(record_id, "/m/talk.mp4")
        v.show()
        return v

    return make, result, original, tmp_path


def test_generate_edit_and_persist(view, monkeypatch, process_events):
    make, result, original, tmp_path = view
    v = make()
    assert v.table.rowCount() == 0 and tr("subs_empty") in v.status.text()
    v._generate()
    cues = v._track.cues
    assert cues and v.table.rowCount() == len(cues)
    assert v._track.transcript_revision == v._revision
    # Edit a cue's text and its end time in the table.
    v.table.item(0, 2).setText("Edited cue text")
    v.table.item(0, 1).setText("00:00:02.500")
    assert v._track.cues[0].text == "Edited cue text" and v._track.cues[0].end == 2.5
    reopened = make()
    assert reopened._track.cues[0].text == "Edited cue text"
    assert [(s.start, s.end, s.text) for s in result.segments] == original   # transcript untouched


def test_problems_are_listed_beside_the_cue_and_a_bad_time_is_refused(view, monkeypatch, process_events):
    make, *_ = view
    v = make()
    v._generate()
    v.table.item(0, 1).setText("00:00:00.100")           # shorter than the minimum, too fast to read
    note = v.table.item(0, 3).text()
    assert tr("subs_issue_too_short") in note or tr("subs_issue_cps_high") in note
    toasts = []
    v.message.connect(lambda text, kind: toasts.append(text))
    before = v._track.cues[1].start
    v.table.item(1, 0).setText("not a time")
    assert toasts == [tr("subs_bad_time")] and v._track.cues[1].start == before


def test_split_merge_delete_and_play(view, process_events):
    make, *_ = view
    v = make()
    v._generate()
    count = len(v._track.cues)
    v.table.selectRow(0)
    v._split()
    assert len(v._track.cues) == count + 1
    v.table.selectRow(0)
    v._merge()
    assert len(v._track.cues) == count
    heard = []
    v.listen_requested.connect(lambda a, b, loop: heard.append((a, b, loop)))
    v.table.selectRow(0)
    v._play()
    assert heard == [(v._track.cues[0].start, v._track.cues[0].end, False)]
    v._delete()
    assert len(v._track.cues) == count - 1


def test_export_writes_srt_and_refuses_a_cue_nobody_could_show(view, monkeypatch, tmp_path, process_events):
    make, *_ = view
    v = make()
    v._generate()
    out = tmp_path / "out.srt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    v._export("srt")
    text = out.read_text(encoding="utf-8")
    assert text.startswith("1\n00:00:00,000 -->") and "Short one." in text
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    out.unlink()
    v.table.item(0, 1).setText("00:00:00.000")         # end == start: cannot be shown
    v._export("srt")
    assert warnings and not out.exists()


def test_a_changed_transcript_marks_the_cues_stale_without_touching_them(view, process_events):
    make, result, *_ = view
    v = make()
    v._generate()
    result.segments[1].text = "Short one, now corrected."
    v.set_result(result)
    assert tr("subs_stale") in v.status.text()
    assert v._track.cues[-1].text == "Short one."        # only an explicit regenerate replaces them


def test_unsaved_record_keeps_nothing_on_disk(view, process_events):
    make, _, _, tmp_path = view
    v = make(record_id=None)
    v._generate()
    assert not list(tmp_path.rglob("subtitles.json"))
