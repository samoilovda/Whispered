"""API upload from the publish dialog: the dialog only validates and asks;
MainWindow runs the worker, reports back to the dialog if it is still open,
opens Studio and records the upload. No network, no real keyring."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtGui import QImage

from core import youtube_upload
from domain.youtube_publish import UploadRecord
from ui import youtube_publish_dialog as dialog_module
from ui.youtube_publish_dialog import YouTubePublishDialog

_TEXTS = {
    "titles": ["First title"],
    "description": "Hook",
    "tags": ", ".join(f"tag{i:03d}" + "x" * 40 for i in range(20)),   # over the 500-char budget
    "language": "en",
    "chapter_check": None,
}


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "talk.mp4"
    path.write_bytes(b"x" * 10)
    return path


def _dialog(video, tmp_path, **kwargs):
    kwargs.setdefault("upload_enabled", True)
    kwargs.setdefault("record_path", tmp_path / "youtube_upload.json")
    kwargs.setdefault("pending_path", tmp_path / "youtube_upload.pending.json")
    return YouTubePublishDialog(_TEXTS, video_path=video, source_name="talk", save_dir=tmp_path, **kwargs)


def test_upload_controls_exist_only_in_api_mode(video, tmp_path):
    assert not hasattr(_dialog(video, tmp_path, upload_enabled=False), "_upload_btn")
    assert _dialog(video, tmp_path)._upload_btn.isEnabled()


def test_upload_emits_a_trimmed_private_package(video, tmp_path):
    dlg = _dialog(video, tmp_path)
    seen = []
    dlg.upload_requested.connect(seen.append)
    dlg._upload_btn.click()
    pkg = seen[0]
    assert pkg.privacy == "private" and pkg.title == "First title" and pkg.video_path == video
    assert 0 < len(pkg.tags) < 20                       # cut to the 500-character budget
    assert not dlg._upload_btn.isEnabled() and not dlg._cancel_upload_btn.isHidden()
    dlg._privacy_combo.setEnabled(True)
    dlg._privacy_combo.setCurrentIndex(1)
    assert dlg.upload_package().privacy == "unlisted"


def test_blocking_issues_disable_the_upload(video, tmp_path):
    dlg = _dialog(video, tmp_path)
    dlg._title_combo.setEditText("")
    assert not dlg._upload_btn.isEnabled()
    seen = []
    dlg.upload_requested.connect(seen.append)
    dlg._start_upload()
    assert seen == []


def test_progress_success_failure_and_cancel_are_reflected(video, tmp_path):
    dlg = _dialog(video, tmp_path)
    dlg._upload_btn.click()
    dlg.set_upload_progress(40, 4, 10)
    assert dlg._upload_progress.value() == 40
    dlg.set_upload_failed("boom")
    assert "boom" in dlg._upload_status.text() and dlg._upload_btn.isEnabled()
    dlg._upload_btn.click()
    dlg.set_upload_cancelled()
    assert dlg._upload_btn.isEnabled() and dlg._cancel_upload_btn.isHidden()
    dlg._upload_btn.click()
    dlg.set_upload_done(UploadRecord("vid", "2026-10-07T10:00:00+00:00", "T", "private"))
    assert "vid" in dlg._upload_status.text()


def test_uploading_twice_asks_first(video, tmp_path, monkeypatch):
    youtube_upload.save_upload_record(
        tmp_path / "youtube_upload.json", UploadRecord("old", "2026-10-01T00:00:00+00:00", "T", "private"))
    dlg = _dialog(video, tmp_path)
    seen = []
    dlg.upload_requested.connect(seen.append)
    monkeypatch.setattr(dlg, "_confirm_duplicate", lambda record: False)
    dlg._upload_btn.click()
    assert seen == []
    monkeypatch.setattr(dlg, "_confirm_duplicate", lambda record: True)
    dlg._upload_btn.click()
    assert len(seen) == 1


def test_an_interrupted_upload_offers_resume_without_the_duplicate_question(video, tmp_path, monkeypatch):
    youtube_upload.save_upload_record(
        tmp_path / "youtube_upload.json", UploadRecord("old", "2026-10-01T00:00:00+00:00", "T", "private"))
    youtube_upload.save_pending(tmp_path / "youtube_upload.pending.json", video, "https://s/1", "T")
    dlg = _dialog(video, tmp_path)
    assert dlg._upload_btn.text() == dialog_module.tr("yt_publish_resume")
    monkeypatch.setattr(dlg, "_confirm_duplicate", lambda r: pytest.fail("must not ask"))
    seen = []
    dlg.upload_requested.connect(seen.append)
    dlg._upload_btn.click()
    assert len(seen) == 1


def test_an_oversized_cover_is_reencoded_as_jpeg(video, tmp_path, monkeypatch):
    cover = tmp_path / "cover.png"
    image = QImage(200, 200, QImage.Format.Format_RGB32)
    for x in range(200):
        for y in range(200):
            image.setPixel(x, y, (x * 255 // 200) << 16 | (y * 255 // 200) << 8 | (x ^ y) & 255)
    assert image.save(str(cover), "PNG")
    monkeypatch.setattr(dialog_module, "THUMBNAIL_MAX_BYTES", cover.stat().st_size - 1)
    dlg = _dialog(video, tmp_path, cover_path=cover)
    shrunk = dlg.upload_package().thumbnail_path
    assert shrunk is not None and shrunk.suffix == ".jpg" and shrunk.is_file()
    monkeypatch.setattr(dialog_module, "THUMBNAIL_MAX_BYTES", 10 ** 9)
    assert dlg.upload_package().thumbnail_path == cover


# ------------------------------------------------------------------ MainWindow

class _FakeDialog:
    def __init__(self):
        self.events = []

    def set_upload_progress(self, percent, sent=None, total=None):
        self.events.append(("progress", percent))

    def set_upload_done(self, record):
        self.events.append(("done", record.video_id))

    def set_upload_failed(self, message):
        self.events.append(("failed", message))

    def set_upload_cancelled(self):
        self.events.append(("cancelled",))

    def set_thumbnail_warning(self, message):
        self.events.append(("warning", message))


def _window(monkeypatch, tmp_path):
    from core.history import HistoryStore
    from transcriber import Segment, TranscriptionResult
    from ui.main_window import MainWindow

    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    window = MainWindow()
    result = TranscriptionResult(segments=[Segment(0.0, 1.0, "hi")], language="en", duration=1.0)
    record_id = store.add(result, source_path="", model="")
    return window, store, record_id


def _fake_worker(monkeypatch, execute):
    from core import youtube_upload_worker

    class _Fake(youtube_upload_worker.YouTubeUploadWorker):
        def _execute(self):
            execute(self)

    monkeypatch.setattr(youtube_upload_worker, "YouTubeUploadWorker", _Fake)


def _start(window, record_id, tmp_path, video):
    from domain.youtube_publish import PublishPackage

    pkg = PublishPackage(video, "T", "D", ("a",), None)
    window._start_youtube_upload(
        pkg, record_id, tmp_path / "youtube_upload.json", tmp_path / "youtube_upload.pending.json")
    assert window._yt_upload is not None
    assert window._yt_upload.wait(5000)


def test_successful_upload_reports_opens_studio_and_marks_the_record(monkeypatch, tmp_path, video, process_events):
    window, store, record_id = _window(monkeypatch, tmp_path)
    monkeypatch.setattr(window, "_youtube_upload_ready", lambda: True)
    opened = []
    monkeypatch.setattr("PyQt6.QtGui.QDesktopServices.openUrl", lambda url: opened.append(url.toString()) or True)

    def execute(worker):
        worker.progress.emit(50, 5, 10)
        worker.uploaded.emit(UploadRecord("vid42", "2026-10-07T10:00:00+00:00", "T", "private"))

    _fake_worker(monkeypatch, execute)
    dialog = _FakeDialog()
    window._yt_dialog = dialog
    _start(window, record_id, tmp_path, video)
    process_events()
    assert dialog.events == [("progress", 50), ("done", "vid42")]
    assert opened == ["https://studio.youtube.com/video/vid42/edit"]
    assert "youtube_upload" in store.get_record(record_id)["artifacts"]
    window.close()


def test_failed_upload_with_expired_login_asks_to_reconnect(monkeypatch, tmp_path, video, process_events):
    window, _store, record_id = _window(monkeypatch, tmp_path)
    monkeypatch.setattr(window, "_youtube_upload_ready", lambda: True)
    _fake_worker(monkeypatch, lambda worker: worker.failed.emit("expired", True))
    dialog = _FakeDialog()
    window._yt_dialog = dialog
    _start(window, record_id, tmp_path, video)
    process_events()
    assert dialog.events == [("failed", dialog_module.tr("yt_publish_relogin"))]
    window.close()


def test_upload_is_refused_without_a_connected_account(monkeypatch, tmp_path, video):
    from domain.youtube_publish import PublishPackage

    window, _store, record_id = _window(monkeypatch, tmp_path)
    monkeypatch.setattr(window, "_youtube_upload_ready", lambda: False)
    dialog = _FakeDialog()
    window._yt_dialog = dialog
    window._start_youtube_upload(
        PublishPackage(video, "T", "D", (), None), record_id,
        tmp_path / "r.json", tmp_path / "p.json")
    assert window._yt_upload is None
    assert dialog.events == [("failed", dialog_module.tr("yt_publish_relogin"))]
    window.close()


def test_upload_ready_requires_api_mode_client_and_login(monkeypatch):
    from config import get_config
    from core import youtube_oauth
    from ui.main_window import MainWindow

    window = MainWindow()
    cfg = get_config()
    monkeypatch.setattr(youtube_oauth, "is_connected", lambda: True)
    cfg.yt_oauth_client_id, cfg.yt_oauth_client_secret = "cid", "csec"
    cfg.yt_publish_mode = "handoff"
    assert not window._youtube_upload_ready()
    cfg.yt_publish_mode = "api"
    assert window._youtube_upload_ready()
    cfg.yt_oauth_client_secret = ""
    assert not window._youtube_upload_ready()
    cfg.yt_oauth_client_secret = "csec"
    monkeypatch.setattr(youtube_oauth, "is_connected", lambda: False)
    assert not window._youtube_upload_ready()
    cfg.yt_oauth_client_id = cfg.yt_oauth_client_secret = ""
    window.close()
