"""application/hermes_speaker.py: finding the user's voice (no models)."""

from __future__ import annotations

import json

import numpy as np

from application import hermes_push as hp
from application import hermes_speaker as hs

ME, GUEST = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])


def _speakers():
    return {
        "speaker_names": {"Speaker 1": "Speaker 1", "Speaker 2": "Гость"},
        "segments": [
            {"start": 0, "end": 5, "speaker_id": "Speaker 1", "speaker": "Speaker 1", "text": "a"},
            {"start": 5, "end": 9, "speaker_id": "Speaker 2", "speaker": "Гость", "text": "b"},
            {"start": 9, "end": 10, "speaker_id": "Speaker 1", "speaker": "Speaker 1", "text": "c"},
        ],
    }


def _embed_by_time(windows):
    # Speaker 1 talks before 5 s, Speaker 2 after.
    return [ME if a < 5 else GUEST for a, _ in windows]


def test_windows_skip_short_turns_trim_edges_and_spread():
    segs = [{"start": i * 3, "end": i * 3 + 3, "speaker_id": "A"} for i in range(10)]
    segs.append({"start": 40, "end": 41, "speaker_id": "A"})
    assert hs.speaker_windows(segs, "A", limit=20)[0] == (0.25, 2.75)
    assert len(hs.speaker_windows(segs, "A", limit=20)) == 10
    assert hs.speaker_windows(segs, "A", limit=5)[1] == (6.25, 8.75)


def test_match_needs_a_high_score_and_a_clear_margin():
    assert hs.match({"A": ME, "B": GUEST}, ME).speaker_id == "A"
    weak = hs.match({"A": hs.centroid([ME, GUEST, GUEST])}, ME)
    assert weak.speaker_id is None and "sounds like" in weak.reason
    twin = hs.centroid([ME, ME, ME, ME, ME, ME, ME, GUEST])
    close = hs.match({"A": ME, "B": twin}, ME)
    assert close.speaker_id is None and "alike" in close.reason


def test_identify_and_mark_the_transcript():
    profile = hs.VoiceProfile(ME, "m.onnx")
    found = hs.identify(_speakers(), profile, _embed_by_time)
    assert found.speaker_id == "Speaker 1"
    marked = hs.mark_speakers_json(_speakers(), found.as_dict("Денис"))
    assert marked["speaker_names"]["Speaker 1"] == "Денис"
    assert [s["is_me"] for s in marked["segments"]] == [True, False, True]
    assert marked["segments"][0]["speaker"] == "Денис"
    md = "- `00:00:00.000` **Speaker 1**: a\n- `00:00:05.000` **Гость**: b\n"
    assert hs.mark_speakers_md(md, "Speaker 1", "Денис").count("**Денис (я)**:") == 1


def test_profile_round_trip_and_fingerprint(tmp_path):
    path = tmp_path / "voice_profile.json"
    hs.save_profile(path, hs.VoiceProfile(ME, "m.onnx", "talk / Speaker 1"))
    loaded = hs.load_profile(path)
    assert loaded is not None and loaded.model == "m.onnx"
    assert loaded.fingerprint() == hs.VoiceProfile(ME, "m.onnx").fingerprint()
    assert loaded.fingerprint() != hs.VoiceProfile(GUEST, "m.onnx").fingerprint()
    assert hs.load_profile(tmp_path / "missing.json") is None


def _stage(tmp_path, with_md=True):
    record = tmp_path / "talk-7"
    record.mkdir()
    media = tmp_path / "talk.m4a"
    media.write_bytes(b"")
    (record / "clean.md.manifest.json").write_text(json.dumps({"source_path": str(media)}))
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / hp.SPEAKERS_JSON).write_text(json.dumps(_speakers()), encoding="utf-8")
    if with_md:
        (stage / hp.SPEAKERS_MD).write_text("**Speaker 1**: a\n**Гость**: b\n", encoding="utf-8")
    item = hp.PushItem(record, (), "d")
    return item, stage, media


def test_annotator_marks_the_staged_transcript(tmp_path):
    item, stage, media = _stage(tmp_path)
    opened = []

    def embedder(path):
        opened.append(path)
        return _embed_by_time

    info = hs.annotator(hs.VoiceProfile(ME, "m.onnx"), "Денис", "m.onnx", embedder)(item, stage)
    assert info["me"]["speaker_id"] == "Speaker 1" and opened == [media]
    sent = json.loads((stage / hp.SPEAKERS_JSON).read_text(encoding="utf-8"))
    assert sent["me"]["name"] == "Денис"
    assert "**Денис (я)**:" in (stage / hp.SPEAKERS_MD).read_text(encoding="utf-8")


def test_annotator_says_why_nobody_is_marked(tmp_path):
    item, stage, _ = _stage(tmp_path)
    no_profile = hs.annotator(None, "Денис", "m.onnx", lambda p: _embed_by_time)(item, stage)
    assert no_profile["me"]["speaker_id"] is None and "--enroll" in no_profile["me"]["reason"]
    other_model = hs.annotator(hs.VoiceProfile(ME, "old.onnx"), "Денис", "m.onnx",
                               lambda p: _embed_by_time)(item, stage)
    assert "another model" in other_model["me"]["reason"]
    (stage / hp.SPEAKERS_JSON).unlink()
    bare = hs.annotator(None, "Денис", "m.onnx", lambda p: _embed_by_time)(item, stage)
    assert bare["me"]["reason"] == "record has no speaker transcript"
