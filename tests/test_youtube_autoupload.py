"""The upload queue the publish wizard fills and tools/youtube_autoupload.py
drains."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from application.youtube_autoupload import (
    QUEUE_FILE, find_queued, load_queued, queue_upload, run_queue,
)
from core.youtube_upload import UploadResult, save_upload_record
from domain.youtube_publish import PublishPackage, UploadRecord


def _package(tmp_path: Path, name: str = "talk") -> PublishPackage:
    video = tmp_path / f"{name}.mp4"
    video.write_bytes(b"video")
    cover = tmp_path / f"{name}.png"
    cover.write_bytes(b"png")
    return PublishPackage(
        video_path=video, title=f"Название {name}", description="Описание\n\n0:00 Начало",
        tags=("психология", "Просвет"), thumbnail_path=cover, privacy="unlisted", language="ru",
    )


def _queue(tmp_path: Path, name: str, record_id: int) -> Path:
    art = tmp_path / "output" / f"{name}-{record_id}"
    queue_upload(art, record_id, _package(tmp_path, name))
    return art


class _FakeUploader:
    def __init__(self, fail_on: str = ""):
        self.calls: list[str] = []
        self.fail_on = fail_on

    def publish(self, pkg, *, pending_path, record_path, on_progress=None, is_cancelled=None):
        self.calls.append(pkg.title)
        if self.fail_on and self.fail_on in pkg.title:
            raise RuntimeError("network down")
        if on_progress:
            on_progress(5, 5)
        record = UploadRecord(video_id=f"id-{len(self.calls)}", uploaded_at="2026-10-08T00:00:00+00:00",
                              title=pkg.title, privacy=pkg.privacy)
        save_upload_record(record_path, record)
        return UploadResult(record, None)


def test_queue_round_trips_the_approved_package(tmp_path):
    pkg = _package(tmp_path)
    path = queue_upload(tmp_path / "art", 46, pkg)
    assert path.name == QUEUE_FILE
    item = load_queued(path)
    assert item is not None and item.record_id == 46
    frozen = tmp_path / "art" / "cover.queued.png"
    assert item.package.thumbnail_path == frozen and frozen.read_bytes() == b"png"
    assert item.package.title == pkg.title and item.package.tags == pkg.tags
    assert load_queued(tmp_path / "missing.json") is None


def test_redrawing_the_cover_later_does_not_change_the_queued_one(tmp_path):
    pkg = _package(tmp_path)
    item = load_queued(queue_upload(tmp_path / "art", 1, pkg))
    pkg.thumbnail_path.write_bytes(b"a newer cover")
    assert item.package.thumbnail_path.read_bytes() == b"png"


def test_find_queued_lists_oldest_first(tmp_path):
    _queue(tmp_path, "first", 1)
    time.sleep(1.1)
    _queue(tmp_path, "second", 2)
    assert [item.record_id for item in find_queued(tmp_path / "output")] == [1, 2]


def test_uploads_and_then_skips_what_is_already_on_youtube(tmp_path):
    _queue(tmp_path, "talk", 1)
    items = find_queued(tmp_path / "output")
    uploader = _FakeUploader()
    uploaded = []
    first = run_queue(items, uploader, on_uploaded=lambda item, record: uploaded.append(record.video_id))
    assert [o.status for o in first] == ["uploaded"] and uploaded == ["id-1"]
    again = run_queue(items, uploader)
    assert again[0].status == "skipped" and again[0].video_id == "id-1"
    assert len(uploader.calls) == 1


def test_one_failure_does_not_stop_the_rest(tmp_path):
    _queue(tmp_path, "broken", 1)
    _queue(tmp_path, "fine", 2)
    outcomes = run_queue(find_queued(tmp_path / "output"), _FakeUploader(fail_on="broken"))
    assert {o.item.record_id: o.status for o in outcomes} == {1: "failed", 2: "uploaded"}
    assert "network down" in next(o.detail for o in outcomes if o.status == "failed")


def test_daily_limit_and_dry_run(tmp_path):
    for n in range(3):
        _queue(tmp_path, f"talk{n}", n)
    items = find_queued(tmp_path / "output")
    outcomes = run_queue(items, None, limit=2)
    assert [o.status for o in outcomes].count("planned") == 2
    assert [o.status for o in outcomes].count("skipped") == 1


def test_a_missing_video_is_reported_not_uploaded(tmp_path):
    art = _queue(tmp_path, "talk", 1)
    (tmp_path / "talk.mp4").unlink()
    outcomes = run_queue(find_queued(tmp_path / "output"), _FakeUploader())
    assert outcomes[0].status == "failed" and "video not found" in outcomes[0].detail
    assert not (art / "youtube_upload.json").exists()


@pytest.fixture
def cli(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "youtube_autoupload_cli", Path(__file__).parent.parent / "tools" / "youtube_autoupload.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "output_dir", lambda: tmp_path / "output")
    return module


def test_cli_with_an_empty_queue(cli, capsys):
    assert cli.main([]) == 0
    assert "Nothing is queued" in capsys.readouterr().out


def test_cli_refuses_without_a_youtube_connection(cli, tmp_path, monkeypatch, capsys):
    import config
    from core import youtube_oauth

    _queue(tmp_path, "talk", 1)
    monkeypatch.setattr(config, "_config", config.Config())
    monkeypatch.setattr(youtube_oauth, "is_connected", lambda: False)
    assert cli.main([]) == 2
    assert "not connected" in capsys.readouterr().out


def test_cli_dry_run_lists_the_plan(cli, tmp_path, capsys):
    _queue(tmp_path, "talk", 1)
    assert cli.main(["--dry-run"]) == 0
    assert "would upload Название talk" in capsys.readouterr().out


def test_updates_only_what_is_already_on_youtube(tmp_path):
    from application.youtube_autoupload import run_updates

    _queue(tmp_path, "talk", 1)
    _queue(tmp_path, "later", 2)
    items = find_queued(tmp_path / "output")
    run_queue([i for i in items if i.record_id == 1], _FakeUploader())

    class _Updater:
        calls: list = []

        def update_metadata(self, video_id, pkg):
            self.calls.append((video_id, pkg.title))
            if pkg.title == "boom":
                raise RuntimeError("quota")

    updater = _Updater()
    assert [o.status for o in run_updates(items, None) if o.item.record_id == 1] == ["planned"]
    outcomes = {o.item.record_id: o for o in run_updates(items, updater)}
    assert outcomes[1].status == "updated" and outcomes[1].video_id == "id-1"
    assert outcomes[2].status == "skipped"           # never uploaded: no update
    assert updater.calls == [("id-1", items[[i.record_id for i in items].index(1)].package.title)]
