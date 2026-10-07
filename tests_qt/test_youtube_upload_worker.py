"""YouTubeUploadWorker: one terminal signal per run, progress as percent,
re-login flagged separately, and WorkerRegistry can retire it mid-upload."""

from __future__ import annotations

import time

import pytest

from core import youtube_oauth, youtube_upload
from core.worker_registry import WorkerRegistry
from core.youtube_upload_worker import YouTubeUploadWorker
from domain.youtube_publish import PublishPackage, UploadRecord


def _worker(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 10)
    pkg = PublishPackage(video, "T", "D", ("a",), None)
    return YouTubeUploadWorker(
        pkg, client_id="cid", client_secret="csec",
        pending_path=tmp_path / "pending.json", record_path=tmp_path / "record.json")


def _run(worker, process_events):
    seen = []
    worker.progress.connect(lambda p, s, t: seen.append(("progress", p)))
    worker.thumbnail_warning.connect(lambda m: seen.append(("warning", m)))
    worker.uploaded.connect(lambda r: seen.append(("uploaded", r.video_id)))
    worker.cancelled.connect(lambda: seen.append(("cancelled",)))
    worker.failed.connect(lambda m, relogin: seen.append(("failed", m, relogin)))
    worker.start()
    assert worker.wait(5000)
    process_events()
    return seen


@pytest.fixture(autouse=True)
def _no_tokens(monkeypatch):
    monkeypatch.setattr(youtube_oauth, "TokenProvider", lambda *a, **k: object())


def _patch_publish(monkeypatch, fn):
    monkeypatch.setattr(youtube_upload.UploadClient, "publish", lambda self, pkg, **kw: fn(**kw))


def test_success_reports_progress_and_the_record(monkeypatch, tmp_path, process_events):
    def publish(**kw):
        kw["on_progress"](5, 10)
        kw["on_progress"](10, 10)
        return youtube_upload.UploadResult(UploadRecord("vid", "now", "T", "private"), "no phone")

    _patch_publish(monkeypatch, publish)
    assert _run(_worker(tmp_path), process_events) == [
        ("progress", 50), ("progress", 100), ("warning", "no phone"), ("uploaded", "vid")]


@pytest.mark.parametrize("error, expected", [
    (youtube_upload.UploadCancelled("c"), ("cancelled",)),
    (youtube_oauth.YouTubeAuthExpired("expired"), ("failed", "expired", True)),
    (youtube_upload.QuotaExceeded("quota"), ("failed", "quota", False)),
    (youtube_oauth.YouTubeAuthError("auth"), ("failed", "auth", False)),
    (RuntimeError("kaput"), ("failed", "kaput", False)),
])
def test_errors_map_to_exactly_one_terminal_signal(monkeypatch, tmp_path, process_events, error, expected):
    def publish(**kw):
        raise error

    _patch_publish(monkeypatch, publish)
    assert _run(_worker(tmp_path), process_events) == [expected]


def test_registry_retire_cancels_a_running_upload(monkeypatch, tmp_path):
    def publish(**kw):
        while not kw["is_cancelled"]():
            time.sleep(0.01)
        raise youtube_upload.UploadCancelled("c")

    _patch_publish(monkeypatch, publish)
    worker = _worker(tmp_path)
    registry = WorkerRegistry()
    registry.register(worker, name="youtube_upload")
    worker.start()
    registry.retire(worker)
    assert worker.wait(5000)
