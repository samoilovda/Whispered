"""The publish wizard keeps its edits per record: closing and reopening the
dialog brings back the agreed texts and names (also into the Cover
workspace), another record starts clean, and a regenerated package is not
silently overridden by a stale draft."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from application.youtube_publish import DRAFT_FILE
from ui.youtube_publish_dialog import PAGE_COVER, YouTubePublishDialog

_TEXTS = {
    "titles": ["1. Почему психологу трудно в личной терапии?", "2. Другое"],
    "description": "Описание\n\n0:00 Вступление\n2:10 Личная терапия",
    "tags": "психология, терапия",
    "language": "ru",
    "chapter_check": None,
}


class _FakeStudio(QObject):
    preview_changed = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.calls: list[tuple] = []

    def set_cover_texts(self, title, host, guest):
        self.calls.append(("texts", title, host, guest))

    def shuffle(self):
        pass

    def choose_photo(self, slot):
        pass

    def grab_frame(self, slot):
        pass

    def has_video(self):
        return False

    def photo_framing(self, slot):
        return (0.5, 0.5), 1.0

    def set_photo_framing(self, slot, focus, zoom):
        pass


def _dialog(draft: Path, texts=None, studio=None) -> YouTubePublishDialog:
    return YouTubePublishDialog(
        texts or _TEXTS, source_name="talk", cover_studio=studio,
        host_name="Денис Самойлов", draft_path=draft,
    )


def _edit(dlg: YouTubePublishDialog) -> None:
    dlg._title_combo.setCurrentText("Мой собственный заголовок")
    dlg._desc_edit.setPlainText("Новое описание\n\n0:00 Вступление")
    dlg._tags_edit.setText("психология, супервизия")
    dlg._guest_edit.setText("Анна Иванова")
    dlg._cover_text_edit.setText("Терапия для терапевта")


def test_edits_survive_closing_and_reopening(tmp_path):
    draft = tmp_path / "rec-1" / DRAFT_FILE
    first = _dialog(draft)
    _edit(first)
    first.reject()
    assert draft.is_file()

    studio = _FakeStudio()
    again = _dialog(draft, studio=studio)
    assert again.current_title() == "Мой собственный заголовок"
    assert again._desc_edit.toPlainText() == "Новое описание\n\n0:00 Вступление"
    assert again.current_tags() == ["психология", "супервизия"]
    assert again.guest_name() == "Анна Иванова"
    assert again.cover_text() == "Терапия для терапевта"
    assert again.host_name() == "Денис Самойлов"
    assert again._draft_note.isHidden()
    # The Cover workspace gets the agreed names back too.
    assert studio.calls == [("texts", "Терапия для терапевта", "Денис Самойлов", "Анна Иванова")]


def test_next_also_saves(tmp_path):
    draft = tmp_path / DRAFT_FILE
    dlg = _dialog(draft, studio=_FakeStudio())
    dlg._guest_edit.setText("Анна Иванова")
    dlg._go_next()
    assert dlg.current_page() == PAGE_COVER
    assert _dialog(draft).guest_name() == "Анна Иванова"


def test_another_record_does_not_inherit_the_edits(tmp_path):
    first = _dialog(tmp_path / "rec-1" / DRAFT_FILE)
    _edit(first)
    first.reject()

    studio = _FakeStudio()
    other = _dialog(tmp_path / "rec-2" / DRAFT_FILE, studio=studio)
    assert other.current_title() == "Почему психологу трудно в личной терапии?"
    assert other._desc_edit.toPlainText() == _TEXTS["description"]
    assert other.guest_name() == ""
    assert other.cover_text() == other.current_title()
    assert studio.calls == []


def test_unchanged_wizard_leaves_no_draft(tmp_path):
    draft = tmp_path / DRAFT_FILE
    _dialog(draft).reject()
    assert not draft.exists()


def test_regenerated_package_wins_over_stale_text_edits(tmp_path):
    draft = tmp_path / DRAFT_FILE
    first = _dialog(draft)
    _edit(first)
    first.reject()

    regenerated = dict(_TEXTS, titles=["Совсем новый заголовок"],
                       description="Свежее описание\n\n0:00 Начало")
    again = _dialog(draft, texts=regenerated)
    assert again.current_title() == "Совсем новый заголовок"
    assert again._desc_edit.toPlainText() == "Свежее описание\n\n0:00 Начало"
    # Tags did not change in the package, so the edit still applies, and
    # the names/cover line are not derived from the package at all.
    assert again.current_tags() == ["психология", "супервизия"]
    assert again.guest_name() == "Анна Иванова"
    assert again.cover_text() == "Терапия для терапевта"
    assert not again._draft_note.isHidden()


def test_main_window_keeps_the_draft_per_record(monkeypatch):
    from ui.main_window import MainWindow

    window = MainWindow()
    panel = window.youtube_panel
    record = {"id": 1}
    monkeypatch.setattr(panel, "has_publishable_content", lambda: True)
    monkeypatch.setattr(panel, "publish_texts", lambda: dict(_TEXTS))
    monkeypatch.setattr(panel, "provenance", lambda: (record["id"], "/nowhere/talk.mp4"))
    seen: list[tuple[str, str]] = []

    def fake_exec(dlg):
        seen.append((dlg.current_title(), dlg.guest_name()))
        if not seen[1:]:
            _edit(dlg)
        dlg.reject()
        return 0

    monkeypatch.setattr(YouTubePublishDialog, "exec", fake_exec)
    window.youtube_publish.open_dialog()
    window.youtube_publish.open_dialog()
    record["id"] = 2
    window.youtube_publish.open_dialog()
    assert seen == [
        ("Почему психологу трудно в личной терапии?", ""),
        ("Мой собственный заголовок", "Анна Иванова"),
        ("Почему психологу трудно в личной терапии?", ""),
    ]
    window.close()
