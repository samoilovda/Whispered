"""The upload queue window is not modal: opening it returns at once (an
upload takes minutes), "Upload now" closes the wizard and starts the run,
and a second opening reuses the window. No network, no real keyring."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from core.worker_registry import WorkerRegistry
from ui.youtube_publish_controller import YouTubePublishController


class _Library:
    def refresh(self) -> None:
        pass


class _Host(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.status_label = QLabel()
        self._registry = WorkerRegistry()
        self.library_view = _Library()

    def _open_settings(self, *_args) -> None:
        pass


@pytest.fixture
def controller(tmp_path: Path, monkeypatch):
    import config
    from core import paths, youtube_oauth, youtube_upload
    from domain.youtube_publish import UploadRecord

    cfg = config.Config()
    cfg.yt_oauth_client_id, cfg.yt_oauth_client_secret = "id", "secret"
    monkeypatch.setattr(config, "_config", cfg)
    monkeypatch.setattr(youtube_oauth, "is_connected", lambda: True)
    monkeypatch.setattr(youtube_oauth, "TokenProvider", lambda *args, **kwargs: None)
    monkeypatch.setattr(paths, "output_dir", lambda: tmp_path / "out")

    class _Client:
        def __init__(self, tokens):
            pass

        def publish(self, pkg, *, pending_path, record_path, on_progress, is_cancelled):
            time.sleep(0.2)
            record = UploadRecord(video_id="v1", uploaded_at="2026-10-26T00:00:00+00:00",
                                  title=pkg.title, privacy=pkg.privacy)
            youtube_upload.save_upload_record(record_path, record)
            return youtube_upload.UploadResult(record, None)

    monkeypatch.setattr(youtube_upload, "UploadClient", _Client)
    host = _Host()
    ctrl = YouTubePublishController(host)
    yield ctrl
    if ctrl.queue is not None:
        ctrl.queue.wait(5000)
    if ctrl.queue_dialog is not None:
        ctrl.queue_dialog.close()


def _queue(tmp_path: Path) -> None:
    from application.youtube_autoupload import queue_upload
    from domain.youtube_publish import PublishPackage

    video = tmp_path / "talk.mp4"
    video.write_bytes(b"video")
    queue_upload(tmp_path / "out" / "talk-1", 1, PublishPackage(
        video_path=video, title="Title", description="Hook", tags=("a",),
        thumbnail_path=None, privacy="private", language="en"))


def _wait_until(condition, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)


def test_the_queue_window_does_not_block(controller, tmp_path):
    _queue(tmp_path)
    started = time.monotonic()
    controller.open_queue(start=True)
    # Returned while the upload is still running: nothing waits on it.
    assert time.monotonic() - started < 0.2 and controller.queue_running()
    dialog = controller.queue_dialog
    assert dialog is not None and dialog.isVisible() and not dialog.isModal()
    controller.open_queue()
    assert controller.queue_dialog is dialog          # one window, raised again
    _wait_until(lambda: not controller.queue_running() and dialog._message.text())
    assert dialog._tree.topLevelItem(0).data(0, 256) == "v1"


def test_upload_now_closes_the_wizard_and_runs_the_queue(controller, tmp_path):
    from PyQt6.QtWidgets import QDialog

    _queue(tmp_path)
    wizard = QDialog()
    wizard.show()
    controller._upload_queued_now(wizard)
    assert not wizard.isVisible()
    _wait_until(lambda: controller.queue_dialog is not None)
    assert controller.queue_dialog is not None and controller.queue_running()


def test_closing_the_window_keeps_the_upload_going(controller, tmp_path):
    _queue(tmp_path)
    controller.open_queue(start=True)
    controller.queue_dialog.close()
    assert controller.queue_dialog is None and controller.queue_running()
    _wait_until(lambda: not controller.queue_running())
    assert (tmp_path / "out" / "talk-1" / "youtube_upload.json").is_file()
