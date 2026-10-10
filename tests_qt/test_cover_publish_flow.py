"""The Cover workspace as the publish wizard drives it, and MainWindow
saving the approved cover through the recipe's own "cover" step."""

from __future__ import annotations

import time

import pytest

from domain.transcription import Segment, TranscriptionResult


def _view():
    from ui.cover_view import CoverView

    return CoverView()


def test_a_guest_switches_the_cover_to_two_speakers():
    view = _view()
    view.set_cover_texts("Почему психологу трудно", "Денис Самойлов", "Валерия Воронина")
    layout, _variant, slots = view.inspector.state()
    assert layout == "duo"
    assert slots["title"] == "Почему психологу трудно"
    assert "Денис Самойлов" in slots["names"] and "Валерия Воронина" in slots["names"]
    assert view.last_image is not None and not view.last_image.isNull()


def test_no_guest_means_the_solo_cover():
    view = _view()
    view.set_cover_texts("Тревога", "Денис Самойлов", "")
    layout, _variant, slots = view.inspector.state()
    assert layout == "solo" and slots["names"] == "Денис Самойлов"


def test_preview_changed_carries_each_render():
    view = _view()
    seen = []
    view.preview_changed.connect(seen.append)
    view.shuffle()
    assert seen and not seen[-1].isNull()


def test_another_recording_drops_the_previous_episode(tmp_path):
    view = _view()
    view.set_provenance(1, str(tmp_path / "first.mp4"))
    view.set_cover_texts("Старое название", "Ведущий", "Гость")
    view.photos["photo_b"] = str(tmp_path / "guest.jpg")
    view.shuffle()

    view.set_provenance(1, str(tmp_path / "first.mp4"))   # same recording
    assert view.inspector.title_edit.toPlainText() == "Старое название"

    view.set_provenance(2, str(tmp_path / "second.mp4"))
    assert view.inspector.title_edit.toPlainText() == ""
    assert "photo_b" not in view.photos
    assert view._shuffle == 0


# ------------------------------------------------------------------ MainWindow

@pytest.fixture
def window(monkeypatch, tmp_path):
    import config
    import core.history as history

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    store = history.HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr(history, "_store", store)
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    from ui.main_window import MainWindow

    win = MainWindow()
    yield win
    win.close()


class _FakeDialog:
    def __init__(self, host: str = "Денис Самойлов"):
        self.ready = None
        self.failed = None
        self._host = host

    def host_name(self):
        return self._host

    def set_cover_ready(self, path):
        self.ready = path

    def set_cover_failed(self, message):
        self.failed = message


def test_approved_cover_is_saved_with_a_reusable_manifest(window, tmp_path, process_events):
    from application.steps import STEP_REGISTRY, StepContext
    from config import get_config
    from infrastructure.persistence import artifact_store

    window._document_session.apply_result(TranscriptionResult(
        segments=[Segment(0.0, 1.0, "привет")], language="ru", duration=1.0,
    ))
    window.cover_view.set_cover_texts("Почему психологу трудно", "Денис Самойлов", "")
    art_dir = tmp_path / "artifacts"
    dialog = _FakeDialog()
    window.youtube_publish.dialog = dialog

    window.youtube_publish.render_cover(dialog, 7, str(tmp_path / "talk.mp4"), art_dir)
    deadline = time.monotonic() + 10
    while dialog.ready is None and dialog.failed is None and time.monotonic() < deadline:
        process_events()
        time.sleep(0.01)

    assert dialog.failed is None
    assert dialog.ready == art_dir / "cover.png" and dialog.ready.is_file()
    # The next recipe run with the same Cover settings is a cache hit.
    context = StepContext(
        source_path=str(tmp_path / "talk.mp4"), result=window._current_result,
        record_id=7, artifact_dir=art_dir, params=window.cover_view.render_params(),
    )
    expected = STEP_REGISTRY["cover"].make_artifact(context)
    assert artifact_store.is_cache_valid(expected.path, expected)
    assert get_config().cover_host_name == "Денис Самойлов"
