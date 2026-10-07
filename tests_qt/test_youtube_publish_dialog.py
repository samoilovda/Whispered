"""YouTubePublishDialog (hand-off mode): fields come from the panel's
texts, copy buttons put the right text on the clipboard, and the reveal /
Studio actions call the OS helpers instead of touching the network."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from ui import youtube_publish_dialog as dialog_module
from ui.youtube_publish_dialog import STUDIO_UPLOAD_URL, YouTubePublishDialog

_TEXTS = {
    "titles": ["1. \"First title\"", "2. Second title"],
    "description": "Hook\n\nTimecodes:\n0:00 Intro",
    "tags": "#alpha, beta, Alpha",
    "language": "en",
    "chapter_check": None,
}


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "talk.mp4"
    path.write_bytes(b"x")
    return path


def _dialog(video, tmp_path, **kwargs):
    return YouTubePublishDialog(
        _TEXTS, video_path=video, cover_path=kwargs.pop("cover_path", None),
        source_name="talk", save_dir=tmp_path / "out", **kwargs,
    )


def test_fields_are_filled_from_the_panel_texts(video, tmp_path):
    dlg = _dialog(video, tmp_path)
    assert [dlg._title_combo.itemText(i) for i in range(dlg._title_combo.count())] == [
        "First title", "Second title"]
    assert dlg.current_title() == "First title"
    assert dlg._tags_edit.text() == "alpha, beta"
    pkg = dlg.package()
    assert pkg.description.startswith("Hook") and pkg.tags == ("alpha", "beta")
    assert pkg.language == "en" and pkg.video_path == video
    assert dlg._issues_label.isHidden()
    assert dlg._title_counter.text() == f"{len('First title')}/100"


def test_copy_buttons_put_the_right_text_on_the_clipboard(video, tmp_path):
    dlg = _dialog(video, tmp_path)
    clipboard = QApplication.clipboard()
    dlg._copy_title()
    assert clipboard.text() == "First title"
    dlg._copy_description()
    assert clipboard.text() == _TEXTS["description"]
    dlg._copy_tags()
    assert clipboard.text() == "alpha, beta"


def test_edits_are_reflected_and_validated(video, tmp_path):
    dlg = _dialog(video, tmp_path)
    dlg._title_combo.setEditText("x" * 101)
    assert not dlg._issues_label.isHidden()
    assert "101" in dlg._issues_label.text()
    dlg._title_combo.setEditText("Fine")
    assert dlg._issues_label.isHidden()


def test_missing_video_is_flagged_and_reveal_disabled(tmp_path):
    dlg = _dialog(None, tmp_path)
    assert not dlg._issues_label.isHidden()
    assert not dlg._reveal_btn.isEnabled()


def test_chapter_warning_is_shown(video, tmp_path):
    from core.youtube_description import check_chapters

    texts = dict(_TEXTS, chapter_check=check_chapters([{"start": 0, "title": "Only"}]))
    dlg = YouTubePublishDialog(texts, video_path=video, save_dir=tmp_path)
    assert "chapters" in dlg._issues_label.text().lower() or "глав" in dlg._issues_label.text()


def test_reveal_and_studio_actions(video, tmp_path, monkeypatch):
    revealed, opened = [], []
    monkeypatch.setattr(dialog_module, "reveal_in_file_manager", lambda p: revealed.append(p) or True)
    monkeypatch.setattr(dialog_module.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    dlg = _dialog(video, tmp_path)
    dlg._reveal_video()
    dlg._open_studio()
    assert revealed == [video]
    assert opened == [STUDIO_UPLOAD_URL]


def test_reveal_falls_back_to_opening_the_folder(video, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(dialog_module, "reveal_in_file_manager", lambda p: False)
    monkeypatch.setattr(dialog_module.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    _dialog(video, tmp_path)._reveal_video()
    assert opened == [str(video.parent)]


def test_save_package_writes_the_folder(video, tmp_path):
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png")
    dlg = _dialog(video, tmp_path, cover_path=cover)
    dlg._save_package()
    folder = tmp_path / "out" / "talk_youtube"
    assert (folder / "title.txt").read_text(encoding="utf-8") == "First title"
    assert (folder / "description.txt").read_text(encoding="utf-8") == _TEXTS["description"]
    assert (folder / "tags.txt").read_text(encoding="utf-8") == "alpha, beta"
    assert (folder / "cover.png").read_bytes() == b"png"
