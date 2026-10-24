"""The upload queue in the app: the dialog lists what is queued and where
each record stands; the controller runs the queue on a worker. No network,
no real keyring."""

from __future__ import annotations

from pathlib import Path

from application.youtube_autoupload import Outcome, find_queued, queue_upload
from core.youtube_upload import save_upload_record
from domain.youtube_publish import PublishPackage, UploadRecord
from ui.youtube_queue_dialog import YouTubeQueueDialog


def _queue(tmp_path: Path, name: str, record_id: int) -> Path:
    video = tmp_path / f"{name}.mp4"
    video.write_bytes(b"video")
    art = tmp_path / "output" / f"{name}-{record_id}"
    queue_upload(art, record_id, PublishPackage(
        video_path=video, title=f"Title {name}", description="Hook", tags=("a",),
        thumbnail_path=None, privacy="private", language="en"))
    return art


def _status(dialog, row: int) -> str:
    return dialog._tree.topLevelItem(row).text(2)


def test_lists_waiting_and_uploaded_records(tmp_path):
    _queue(tmp_path, "new", 1)
    done = _queue(tmp_path, "old", 2)
    save_upload_record(done / "youtube_upload.json", UploadRecord(
        video_id="abc", uploaded_at="2026-10-08T00:00:00+00:00", title="Title old",
        privacy="private"))
    dialog = YouTubeQueueDialog(tmp_path / "output", connected=True)
    assert dialog._tree.topLevelItemCount() == 2
    assert dialog.waiting_count() == 1
    assert dialog._upload_btn.isEnabled() and dialog._update_btn.isEnabled()
    assert dialog._settings_btn.isHidden()


def test_not_connected_points_to_settings(tmp_path):
    _queue(tmp_path, "new", 1)
    dialog = YouTubeQueueDialog(tmp_path / "output", connected=False)
    assert not dialog._upload_btn.isEnabled()
    assert not dialog._settings_btn.isHidden()
    requested = []
    dialog.settings_requested.connect(lambda: requested.append(True))
    dialog._settings_btn.click()
    assert requested == [True]


def test_run_progress_and_outcomes(tmp_path):
    _queue(tmp_path, "new", 1)
    dialog = YouTubeQueueDialog(tmp_path / "output", connected=True)
    asked = []
    dialog.run_requested.connect(asked.append)
    dialog._upload_btn.click()
    assert asked == [False]
    dialog.set_running(True)
    assert not dialog._upload_btn.isEnabled() and not dialog._cancel_btn.isHidden()
    item = find_queued(tmp_path / "output")[0]
    dialog.set_progress(item, 40)
    assert "40" in _status(dialog, 0)
    dialog.set_outcomes([Outcome(item, "uploaded", video_id="xyz")])
    assert dialog._cancel_btn.isHidden()
    assert dialog._tree.topLevelItem(0).data(0, 256) == "xyz"


def test_an_expired_login_asks_to_connect_again(tmp_path):
    _queue(tmp_path, "new", 1)
    dialog = YouTubeQueueDialog(tmp_path / "output", connected=True)
    item = find_queued(tmp_path / "output")[0]
    dialog.set_outcomes([Outcome(item, "failed", "expired", relogin=True)])
    assert not dialog._settings_btn.isHidden()
    assert not dialog._upload_btn.isEnabled()


def test_worker_runs_the_queue(tmp_path, monkeypatch):
    from core import youtube_oauth, youtube_upload
    from core.youtube_upload import UploadResult
    from ui.youtube_queue_worker import YouTubeQueueWorker

    _queue(tmp_path, "new", 1)

    class _Client:
        def __init__(self, tokens):
            pass

        def publish(self, pkg, *, pending_path, record_path, on_progress, is_cancelled):
            on_progress(5, 10)
            record = UploadRecord(video_id="v1", uploaded_at="2026-10-08T00:00:00+00:00",
                                  title=pkg.title, privacy=pkg.privacy)
            save_upload_record(record_path, record)
            return UploadResult(record, None)

    monkeypatch.setattr(youtube_upload, "UploadClient", _Client)
    monkeypatch.setattr(youtube_oauth, "TokenProvider", lambda *args: None)
    worker = YouTubeQueueWorker(tmp_path / "output", client_id="id", client_secret="secret")
    progress, uploaded, done = [], [], []
    worker.progress.connect(lambda item, percent: progress.append(percent))
    worker.uploaded.connect(lambda item, record: uploaded.append(record.video_id))
    worker.done.connect(done.append)
    worker.run()   # synchronously: the signals arrive directly
    assert progress == [50] and uploaded == ["v1"]
    assert [o.status for o in done[0]] == ["uploaded"]
