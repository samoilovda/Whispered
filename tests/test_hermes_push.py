"""application/hermes_push.py: what is pending and what gets sent (no SSH)."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from application import hermes_push as hp


def _folder(root, name="talk-7", extra=()):
    folder = root / name
    folder.mkdir()
    for fname in hp.REQUIRED_FILES:
        (folder / fname).write_text("{}", encoding="utf-8")
    for fname in extra:
        (folder / fname).write_text("x", encoding="utf-8")
    return folder


class _Runner:
    def __init__(self, fail_on=None):
        self.cmds = []
        self.fail_on = fail_on

    def __call__(self, cmd):
        self.cmds.append(list(cmd))
        code = 1 if self.fail_on and cmd[0] == self.fail_on else 0
        return subprocess.CompletedProcess(cmd, code, "", "boom" if code else "")


def test_only_uploaded_records_with_a_clean_text_go(tmp_path):
    (tmp_path / "plain-1").mkdir()
    not_uploaded = tmp_path / "draft-2"
    not_uploaded.mkdir()
    (not_uploaded / hp.CLEAN_MD).write_text("x", encoding="utf-8")
    (not_uploaded / hp.SPEAKERS_JSON).write_text("{}", encoding="utf-8")
    _folder(tmp_path)
    assert [i.artifact_dir.name for i in hp.find_pending(tmp_path)] == ["talk-7"]


def test_optional_files_are_included_when_present(tmp_path):
    _folder(tmp_path, extra=(hp.SPEAKERS_JSON, "youtube_package.json", "cover.png"))
    (item,) = hp.find_pending(tmp_path)
    assert [p.name for p in item.files] == [
        hp.CLEAN_MD, hp.UPLOAD_JSON, hp.SPEAKERS_JSON, "youtube_package.json",
    ]


def test_a_new_salt_sends_everything_again(tmp_path):
    _folder(tmp_path)
    hp.run_push(hp.find_pending(tmp_path, salt="a"), "me@srv:/i", runner=_Runner(), which=lambda n: "x")
    assert hp.find_pending(tmp_path, salt="a") == []
    assert len(hp.find_pending(tmp_path, salt="b")) == 1


def test_annotate_rewrites_the_staged_copy_only(tmp_path):
    folder = _folder(tmp_path)
    seen = {}

    (folder / hp.UPLOAD_JSON).chmod(0o600)

    def annotate(item, stage):
        seen["mode"] = (stage / hp.UPLOAD_JSON).stat().st_mode & 0o777
        (stage / hp.CLEAN_MD).write_text("changed", encoding="utf-8")
        return {"me": {"speaker_id": "S1"}}

    def runner(cmd):
        if cmd[0] == "rsync" and cmd[-2].endswith(hp.READY_FILE):
            seen["ready"] = json.loads(open(cmd[-2], encoding="utf-8").read())
        elif cmd[0] == "rsync":
            seen["clean"] = open(next(c for c in cmd if c.endswith(hp.CLEAN_MD))).read()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    (outcome,) = hp.run_push(hp.find_pending(tmp_path), "me@srv:/i", annotate=annotate,
                             runner=runner, which=lambda n: "x")
    assert outcome.info == {"me": {"speaker_id": "S1"}}
    if os.name != "nt":  # Windows chmod only toggles read-only: st_mode is 0o666
        assert seen["mode"] == 0o640   # group-readable on the server
    assert seen["clean"] == "changed" and seen["ready"]["me"] == {"speaker_id": "S1"}
    assert (folder / hp.CLEAN_MD).read_text() == "{}"


def test_push_sends_files_then_ready_and_marks_the_folder(tmp_path):
    folder = _folder(tmp_path)
    runner = _Runner()
    (item,) = hp.find_pending(tmp_path)

    (outcome,) = hp.run_push([item], "me@srv:/inbox/", runner=runner, which=lambda n: "/usr/bin/rsync")

    assert outcome.status == "pushed"
    assert runner.cmds[0][:1] == ["ssh"] and runner.cmds[0][-1] == "/inbox/talk-7"
    assert runner.cmds[1][0] == "rsync" and runner.cmds[1][-1] == "me@srv:/inbox/talk-7/"
    assert runner.cmds[2][-2].endswith(hp.READY_FILE)  # completion marker last
    assert not (folder / hp.READY_FILE).exists()
    assert hp.find_pending(tmp_path) == []             # idempotent
    (folder / hp.CLEAN_MD).write_text("edited", encoding="utf-8")
    assert len(hp.find_pending(tmp_path)) == 1         # changed → sent again


def test_falls_back_to_scp_without_rsync(tmp_path):
    _folder(tmp_path)
    runner = _Runner()
    hp.run_push(hp.find_pending(tmp_path), "me@srv:/inbox", runner=runner, which=lambda n: None)
    assert runner.cmds[1][0] == "scp"


def test_a_failed_transfer_is_reported_and_retried_next_time(tmp_path):
    _folder(tmp_path)
    (outcome,) = hp.run_push(
        hp.find_pending(tmp_path), "me@srv:/inbox",
        runner=_Runner(fail_on="rsync"), which=lambda n: "x",
    )
    assert outcome.status == "failed" and "boom" in outcome.detail
    assert len(hp.find_pending(tmp_path)) == 1


def test_dry_run_touches_nothing(tmp_path):
    _folder(tmp_path)
    runner = _Runner()
    (outcome,) = hp.run_push(hp.find_pending(tmp_path), "me@srv:/inbox", dry_run=True, runner=runner)
    assert outcome.status == "planned" and runner.cmds == []


@pytest.mark.parametrize("bad", ["", "srv", "srv:", ":/x", "/local/path:/x"])
def test_target_must_be_user_at_host_colon_path(bad):
    with pytest.raises(ValueError):
        hp.split_target(bad)


def test_marker_json_records_the_digest(tmp_path):
    folder = _folder(tmp_path)
    hp.run_push(hp.find_pending(tmp_path), "me@srv:/i", runner=_Runner(), which=lambda n: "x")
    assert json.loads((folder / hp.MARKER_FILE).read_text())["digest"]


def _payload(*texts):
    return {
        "language": "ru", "duration": 30.0,
        "segments": [
            {"start": float(i * 10), "end": float(i * 10 + 9), "text": t, "speaker": None, "words": []}
            for i, t in enumerate(texts)
        ],
    }


def test_a_record_without_speakers_gets_a_timed_transcript(tmp_path):
    folder = _folder(tmp_path, name="talk-48")
    asked = []

    def payload_for(record_id):
        asked.append(record_id)
        return _payload("Первая мысль.", "  ", "Вторая мысль.")

    (written,) = hp.write_timed_transcripts(tmp_path, payload_for)
    assert asked == [48]
    data = json.loads(written.read_text(encoding="utf-8"))
    assert data["segments"] == [
        {"start": 0.0, "end": 9.0, "text": "Первая мысль."},
        {"start": 20.0, "end": 29.0, "text": "Вторая мысль."},
    ]
    (item,) = hp.find_pending(tmp_path)
    assert hp.TIMED_JSON in [p.name for p in item.files]
    assert folder / hp.TIMED_JSON == written


def test_the_timed_transcript_is_not_rewritten_when_unchanged(tmp_path):
    _folder(tmp_path, name="talk-48")
    hp.write_timed_transcripts(tmp_path, lambda _id: _payload("Мысль."))
    assert hp.write_timed_transcripts(tmp_path, lambda _id: _payload("Мысль.")) == []


def test_records_with_speakers_or_without_a_payload_are_left_alone(tmp_path):
    _folder(tmp_path, name="talk-1", extra=(hp.SPEAKERS_JSON,))
    _folder(tmp_path, name="talk-2")
    _folder(tmp_path, name="no-id")
    assert hp.write_timed_transcripts(tmp_path, lambda _id: None) == []
    assert hp.write_timed_transcripts(
        tmp_path, lambda i: _payload("Мысль.") if i == 1 else None
    ) == []
