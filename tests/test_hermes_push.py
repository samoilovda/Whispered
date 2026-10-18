"""application/hermes_push.py: what is pending and what gets sent (no SSH)."""

from __future__ import annotations

import json
import subprocess

import pytest

from application import hermes_push as hp


def _folder(root, name="talk-7", extra=()):
    folder = root / name
    folder.mkdir()
    (folder / hp.SPEAKERS_JSON).write_text('{"segments": []}', encoding="utf-8")
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


def test_folders_without_a_speaker_transcript_are_ignored(tmp_path):
    (tmp_path / "plain-1").mkdir()
    _folder(tmp_path)
    assert [i.artifact_dir.name for i in hp.find_pending(tmp_path)] == ["talk-7"]


def test_optional_files_are_included_when_present(tmp_path):
    _folder(tmp_path, extra=("youtube_package.json", "cover.png"))
    (item,) = hp.find_pending(tmp_path)
    assert [p.name for p in item.files] == [hp.SPEAKERS_JSON, "youtube_package.json"]


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
    (folder / hp.SPEAKERS_JSON).write_text('{"segments": [1]}', encoding="utf-8")
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
