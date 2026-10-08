"""The publish dialog as a wizard: texts → cover → publish. The cover step
drives the Cover workspace (here a fake with the same surface) and only
moves on once the owner reports the approved cover.png saved."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QImage

from ui.youtube_publish_dialog import (
    PAGE_COVER, PAGE_PUBLISH, PAGE_TEXTS, YouTubePublishDialog,
)

_TEXTS = {
    "titles": ["1. Почему психологу трудно в личной терапии?", "2. Другое"],
    "description": "Описание\n\n0:00 Вступление\n2:10 Личная терапия",
    "tags": "психология, терапия",
    "language": "ru",
    "chapter_check": None,
}


class _FakeStudio(QObject):
    preview_changed = pyqtSignal(object)

    def __init__(self, video: bool = True):
        super().__init__()
        self.calls: list[tuple] = []
        self._video = video

    def set_cover_texts(self, title, host, guest):
        self.calls.append(("texts", title, host, guest))
        image = QImage(1280, 720, QImage.Format.Format_ARGB32)
        image.fill(QColor("#D8C8AD"))
        self.preview_changed.emit(image)

    def shuffle(self):
        self.calls.append(("shuffle",))

    def choose_photo(self, slot):
        self.calls.append(("photo", slot))

    def grab_frame(self, slot):
        self.calls.append(("frame", slot))

    def has_video(self):
        return self._video

    def photo_framing(self, slot):
        return (0.5, 0.15), 1.5

    def set_photo_framing(self, slot, focus, zoom):
        self.calls.append(("framing", slot, focus, zoom))


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "talk.mp4"
    path.write_bytes(b"x")
    return path


def _wizard(video, tmp_path, studio=None, **kwargs):
    return YouTubePublishDialog(
        _TEXTS, video_path=video, source_name="talk", save_dir=tmp_path / "out",
        cover_studio=studio, host_name="Денис Самойлов", **kwargs,
    )


def test_starts_on_the_texts_with_the_host_prefilled(video, tmp_path):
    dlg = _wizard(video, tmp_path, _FakeStudio())
    assert dlg.current_page() == PAGE_TEXTS
    assert dlg.host_name() == "Денис Самойлов"
    assert dlg.current_title() == "Почему психологу трудно в личной терапии?"
    assert "0:00 Вступление" in dlg._desc_edit.toPlainText()
    assert dlg._back_btn.isHidden()


def test_no_title_no_next(video, tmp_path):
    dlg = _wizard(video, tmp_path, _FakeStudio())
    dlg._title_combo.setEditText("")
    assert not dlg._next_btn.isEnabled()


def test_cover_step_renders_from_the_agreed_texts(video, tmp_path):
    studio = _FakeStudio()
    dlg = _wizard(video, tmp_path, studio)
    dlg._title_combo.setEditText("Исправленное название")
    dlg._guest_edit.setText("Валерия Воронина")
    dlg._next_btn.click()
    assert dlg.current_page() == PAGE_COVER
    assert studio.calls[-1] == (
        "texts", "Исправленное название", "Денис Самойлов", "Валерия Воронина")
    assert dlg._cover_preview.pixmap() is not None
    assert not dlg._cover_preview.pixmap().isNull()


def test_cover_text_can_differ_from_the_video_title(video, tmp_path):
    studio = _FakeStudio()
    dlg = _wizard(video, tmp_path, studio)
    dlg._cover_text_edit.setText("Короче для обложки")
    dlg._next_btn.click()
    assert studio.calls[-1][1] == "Короче для обложки"


def test_regenerate_and_guest_photo_drive_the_studio(video, tmp_path):
    studio = _FakeStudio()
    dlg = _wizard(video, tmp_path, studio)
    dlg._next_btn.click()
    dlg._regenerate_btn.click()
    dlg._guest_photo_btn.click()
    dlg._guest_frame_btn.click()
    assert ("shuffle",) in studio.calls
    assert ("photo", "photo_b") in studio.calls
    assert ("frame", "photo_b") in studio.calls


def test_guest_photo_framing_drives_the_studio(video, tmp_path):
    studio = _FakeStudio()
    dlg = _wizard(video, tmp_path, studio)
    dlg._next_btn.click()
    framing = dlg._guest_framing
    # Opens on the workspace's current crop for the second speaker.
    assert framing.framing() == ((0.5, 0.15), 1.5)
    assert not any(call[0] == "framing" for call in studio.calls)
    framing.zoom_slider.setValue(220)
    assert studio.calls[-1] == ("framing", "photo_b", (0.5, 0.15), 2.2)
    framing.focus_combo.setCurrentIndex(0)  # "center"
    assert studio.calls[-1] == ("framing", "photo_b", (0.5, 0.5), 2.2)


def test_frame_button_needs_a_video(video, tmp_path):
    dlg = _wizard(video, tmp_path, _FakeStudio(video=False))
    dlg._next_btn.click()
    assert not dlg._guest_frame_btn.isEnabled()


def test_approving_waits_for_the_saved_cover(video, tmp_path):
    dlg = _wizard(video, tmp_path, _FakeStudio())
    requested = []
    dlg.cover_render_requested.connect(lambda: requested.append(True))
    dlg._next_btn.click()
    dlg._next_btn.click()
    assert requested == [True]
    assert dlg.current_page() == PAGE_COVER
    assert not dlg._next_btn.isEnabled() and not dlg._regenerate_btn.isEnabled()

    cover = tmp_path / "cover.png"
    image = QImage(1280, 720, QImage.Format.Format_ARGB32)
    image.fill(QColor("#726858"))
    image.save(str(cover))
    dlg.set_cover_ready(cover)
    assert dlg.current_page() == PAGE_PUBLISH
    assert dlg.package().thumbnail_path == cover
    assert not dlg._cover_label.pixmap().isNull()


def test_a_failed_cover_stays_on_the_cover_step(video, tmp_path):
    dlg = _wizard(video, tmp_path, _FakeStudio())
    dlg._next_btn.click()
    dlg._next_btn.click()
    dlg.set_cover_failed("disk full")
    assert dlg.current_page() == PAGE_COVER
    assert "disk full" in dlg._cover_status.text()
    assert dlg._next_btn.isEnabled()


def test_back_returns_to_the_texts(video, tmp_path):
    dlg = _wizard(video, tmp_path, _FakeStudio())
    dlg._next_btn.click()
    dlg._back_btn.click()
    assert dlg.current_page() == PAGE_TEXTS


def test_without_a_cover_studio_approval_just_moves_on(video, tmp_path):
    dlg = _wizard(video, tmp_path, None)
    requested = []
    dlg.cover_render_requested.connect(lambda: requested.append(True))
    dlg._next_btn.click()
    assert dlg._regenerate_btn.isHidden()
    dlg._next_btn.click()
    assert dlg.current_page() == PAGE_PUBLISH and requested == []
