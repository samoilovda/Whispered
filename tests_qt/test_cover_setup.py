"""A record's cover setup (application/cover_setup.py) survives a restart:
reopening the record shows and re-renders the same cover, and another
record never inherits it."""

from __future__ import annotations

import json

import pytest

from core.history import HistoryStore
from transcriber import Segment, TranscriptionResult


@pytest.fixture(autouse=True)
def fresh_config(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    return config._config


def _photo(path, color) -> str:
    from PyQt6.QtGui import QColor, QImage

    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    for x in range(0, 64, 8):   # something for the framing to move
        for y in range(48):
            image.setPixelColor(x, y, QColor("white"))
    assert image.save(str(path))
    return str(path)


def _view():
    from ui.cover_view import CoverView

    return CoverView()


def _set_up(view, record_id, source, guest_photo) -> None:
    """Everything the task asks to keep: layout via a guest, an explicit
    variant pick is left on "auto" so the shuffle count matters, a guest
    photo with focus/zoom, two shuffles."""
    view.set_provenance(record_id, source)
    view.set_cover_texts("Почему психологу трудно", "Денис Самойлов", "Валерия Воронина")
    view.photos["photo_b"] = guest_photo
    view.set_photo_framing("photo_b", (0.5, 0.25), 1.6)
    view.shuffle()
    view.shuffle()


def _reopen(record_id, source):
    view = _view()
    view.set_provenance(record_id, source)
    view.render_preview()
    return view


def test_restart_brings_back_the_same_cover(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    guest = _photo(scratch / "frame.png", "darkred")
    source = str(tmp_path / "talk.mp4")

    view = _view()
    _set_up(view, 7, source, guest)
    params, image = view.render_params(), view.last_image.copy()
    view.shutdown()
    view.close()
    # The grabbed still lived in a scratch dir that goes with the app.
    (scratch / "frame.png").unlink()

    again = _reopen(7, source)
    assert again.render_params() == params
    assert again._shuffle == 2
    assert again.photo_framing("photo_b") == ((0.5, 0.25), 1.6)
    assert again.inspector.layout_combo.currentData() == "duo"
    assert again.last_image == image
    stored = params["cover_slots"]["photo_b"]["file"]
    assert "cover_photos" in stored and stored != guest
    again.shutdown()
    again.close()


def test_explicit_variant_and_layout_are_kept(tmp_path):
    source = str(tmp_path / "talk.mp4")
    view = _view()
    view.set_provenance(3, source)
    combo = view.inspector.variant_combo
    combo.setCurrentIndex(combo.count() - 1)
    chosen = combo.currentData()
    view.inspector.layout_combo.setCurrentIndex(view.inspector.layout_combo.findData("solo"))
    view.render_preview()
    params = view.render_params()
    view.shutdown()

    again = _reopen(3, source)
    assert again.inspector.variant_combo.currentData() == chosen
    assert again.render_params() == params


def test_another_record_does_not_inherit_it(tmp_path):
    guest = _photo(tmp_path / "guest.png", "navy")
    view = _view()
    _set_up(view, 7, str(tmp_path / "talk.mp4"), guest)
    view.shutdown()

    other = _reopen(8, str(tmp_path / "other.mp4"))
    assert "photo_b" not in other.photos
    assert other._shuffle == 0
    assert other.inspector.title_edit.toPlainText() == ""


def test_switching_records_in_one_window_restores_each(tmp_path):
    guest = _photo(tmp_path / "guest.png", "navy")
    view = _view()
    _set_up(view, 7, str(tmp_path / "talk.mp4"), guest)
    params = view.render_params()

    view.set_provenance(8, str(tmp_path / "other.mp4"))
    assert "photo_b" not in view.photos and view._shuffle == 0
    view.render_preview()

    view.set_provenance(7, str(tmp_path / "talk.mp4"))
    view.render_preview()
    assert view.render_params() == params


def test_a_new_recording_of_the_same_file_starts_clean(tmp_path):
    guest = _photo(tmp_path / "guest.png", "navy")
    source = str(tmp_path / "talk.mp4")
    view = _view()
    _set_up(view, 7, source, guest)

    view.set_provenance(9, source)   # transcribed again: a new record
    assert "photo_b" not in view.photos and view._shuffle == 0


def test_saving_an_unsaved_cover_keeps_it(tmp_path):
    """A save that first assigns a record id keeps what is on screen — and
    from then on it is that record's setup."""
    guest = _photo(tmp_path / "guest.png", "navy")
    source = str(tmp_path / "talk.mp4")
    view = _view()
    view.set_provenance(None, source)
    view.photos["photo_b"] = guest
    view.shuffle()

    view.set_provenance(11, source)
    assert view._shuffle == 1 and "photo_b" in view.photos
    view.render_preview()
    params = view.render_params()
    view.shutdown()
    assert _reopen(11, source).render_params() == params


def test_host_photo_from_settings_stays_the_fallback(tmp_path, fresh_config):
    from application.cover_setup import SETUP_FILE
    from core.paths import artifact_dir

    host = _photo(tmp_path / "host.png", "darkgreen")
    fresh_config.cover_host_photo = host
    source = str(tmp_path / "talk.mp4")
    view = _view()
    view.set_provenance(5, source)
    view.render_preview()
    view.shutdown()

    data = json.loads((artifact_dir(5, source) / SETUP_FILE).read_text(encoding="utf-8"))
    assert data["photos"]["photo_a"]["file"] == "host"   # not copied

    # A new host photo in Settings is what the record shows next time.
    fresh_config.cover_host_photo = _photo(tmp_path / "host2.png", "olive")
    again = _reopen(5, source)
    assert again.photos["photo_a"] == fresh_config.cover_host_photo


def test_a_broken_setup_file_is_ignored(tmp_path):
    from application.cover_setup import SETUP_FILE
    from core.paths import artifact_dir

    source = str(tmp_path / "talk.mp4")
    folder = artifact_dir(4, source)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / SETUP_FILE).write_text("{not json", encoding="utf-8")
    view = _reopen(4, source)
    assert view._shuffle == 0 and "photo_b" not in view.photos


# ------------------------------------------------------------------ MainWindow

def _make_window(monkeypatch, store):
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    from ui.main_window import MainWindow

    return MainWindow()


def test_main_window_restart_restores_the_records_cover(monkeypatch, tmp_path):
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    result = TranscriptionResult(segments=[Segment(0.0, 5.0, "привет")], language="ru", duration=5.0)
    record = store.add(result, source_path="/media/talk.mp4", model="")
    other = store.add(result, source_path="/media/other.mp4", model="")
    guest = _photo(tmp_path / "guest.png", "darkred")

    first = _make_window(monkeypatch, store)
    assert first._load_from_history(record)
    first.cover_view.set_cover_texts("Тревога", "Денис Самойлов", "Гость")
    first.cover_view.photos["photo_b"] = guest
    first.cover_view.shuffle()
    params, image = first.cover_view.render_params(), first.cover_view.last_image.copy()
    first.close()

    second = _make_window(monkeypatch, store)
    try:
        assert second._load_from_history(record)
        second.cover_view.render_preview()
        assert second.cover_view.render_params() == params
        assert second.cover_view.last_image == image

        assert second._load_from_history(other)
        assert "photo_b" not in second.cover_view.photos
        assert second.cover_view._shuffle == 0
    finally:
        second.close()
