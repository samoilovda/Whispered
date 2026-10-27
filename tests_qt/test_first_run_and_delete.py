"""First-run card, delete flow and settings search (UX-16 / T16)."""

from __future__ import annotations

import pytest

import config
from core.history import HistoryStore
from core.i18n import load_locale, set_locale, tr
from transcriber import Segment, TranscriptionResult


def _result():
    return TranscriptionResult(segments=[Segment(0.0, 1.0, "hello")], language="en", duration=1.0)


@pytest.fixture
def env(monkeypatch, tmp_path, process_events):
    load_locale("en")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    return store, tmp_path


def _window(process_events):
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()
    return window


def test_first_run_card_shows_for_an_empty_library_and_skipping_blocks_nothing(env, process_events):
    window = _window(process_events)
    card = window.start_view._first_run_card
    assert not card.isHidden()
    assert "MB" in window.start_view._first_run_status.text() or "ready" in \
        window.start_view._first_run_status.text()
    assert window.start_view.source_changed  # source switching still works
    window.start_view.dismiss_first_run()
    assert card.isHidden() and config.get_config().first_run_done
    window.close()
    process_events()
    again = _window(process_events)
    assert again.start_view._first_run_card.isHidden()
    again.close()
    process_events()


def test_first_run_card_is_not_shown_to_someone_with_records(env, process_events):
    store, _ = env
    store.add(_result(), source_path="", source_name="a.mp3")
    window = _window(process_events)
    assert window.start_view._first_run_card.isHidden()
    window.close()
    process_events()


def test_choosing_a_profile_sets_the_exact_settings(env, process_events):
    window = _window(process_events)
    combo = window.start_view._profile_combo
    combo.setCurrentIndex(combo.findData("accurate"))
    cfg = config.get_config()
    assert (cfg.default_model, cfg.performance_mode) == ("large-v3-turbo-q8_0", "balanced")
    assert window.model_combo.currentData() == "large-v3-turbo-q8_0"
    window.close()
    process_events()


def test_delete_offers_files_and_moves_them_to_trash_only_on_request(env, monkeypatch, process_events):
    store, tmp_path = env
    rid = store.add(_result(), source_path="", source_name="a.mp3")
    window = _window(process_events)
    window._open_record_view(rid)
    folder = tmp_path / "gen"
    folder.mkdir()
    (folder / "book.md").write_text("x" * 2048)
    trashed = []
    monkeypatch.setattr("PyQt6.QtCore.QFile.moveToTrash", staticmethod(lambda p: trashed.append(p) or True))
    window._ask_delete = lambda box: next(
        b for b in box.buttons() if tr("history_delete_with_files", size="2.0 KB") == b.text())
    window._confirm_delete(rid, str(folder), 2048, 1)
    assert trashed == [str(folder)] and store.get_record(rid) is None

    rid2 = store.add(_result(), source_path="", source_name="b.mp3")
    window._open_record_view(rid2)
    window._ask_delete = lambda box: next(
        b for b in box.buttons() if b.text() == tr("history_delete_record_only"))
    window._confirm_delete(rid2, str(folder), 2048, 1)
    assert trashed == [str(folder)] and store.get_record(rid2) is None  # files untouched
    window.close()
    process_events()


def test_settings_search_finds_controls_in_the_new_language(env, process_events):
    from ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    dialog._search_edit.setText("диариз")
    assert all(dialog._categories.item(i).isHidden() for i in range(dialog._categories.count()))
    set_locale("ru")
    dialog.retranslate()
    shown = [i for i in range(dialog._categories.count()) if not dialog._categories.item(i).isHidden()]
    assert shown, "Russian search must find its category after a live language switch"
    load_locale("en")
    dialog.close()


def test_library_delete_closes_the_open_record(env, monkeypatch, process_events):
    from PyQt6.QtWidgets import QMessageBox

    store, _ = env
    rid = store.add(_result(), source_path="", source_name="a.mp3")
    window = _window(process_events)
    window._open_record_view(rid)
    assert window._last_record_id == rid
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.library_view._delete_record(rid)
    process_events()
    assert window._last_record_id is None
    assert window.transcript_view.get_result() is None
    window.close()
    process_events()


def test_library_clear_all_closes_the_open_record(env, monkeypatch, process_events):
    from PyQt6.QtWidgets import QMessageBox

    store, _ = env
    rid = store.add(_result(), source_path="", source_name="a.mp3")
    window = _window(process_events)
    window._open_record_view(rid)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.library_view._clear_all()
    process_events()
    assert window._last_record_id is None
    window.close()
    process_events()


def test_a_running_recipe_blocks_deleting_its_record(env, monkeypatch, process_events):
    from PyQt6.QtWidgets import QMessageBox

    store, _ = env
    rid = store.add(_result(), source_path="", source_name="a.mp3")
    window = _window(process_events)
    window._open_record_view(rid)
    monkeypatch.setattr(window.recipe_run, "is_running", lambda: True)
    window.recipe_run.record_id = rid
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(1))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    window.library_view._delete_record(rid)
    assert not asked and store.get_record(rid) is not None and window._last_record_id == rid
    window.close()
    process_events()


def test_a_vanished_record_is_reported_as_not_saved(env, process_events):
    store, _ = env
    rid = store.add(_result(), source_path="", source_name="a.mp3")
    window = _window(process_events)
    window._open_record_view(rid)
    store.delete(rid)                                  # behind the window's back
    assert window._persist_transcript() is False
    assert window._last_persist_ok is False
    window._last_persist_ok = True                     # closing would otherwise ask to retry
    window.close()
    process_events()


def test_declining_the_model_download_keeps_the_open_record(env, monkeypatch, tmp_path, process_events):
    store, _ = env
    rid = store.add(_result(), source_path="", source_name="a.mp3")
    window = _window(process_events)
    window._open_record_view(rid)
    media = tmp_path / "new.mp3"
    media.write_bytes(b"x")
    monkeypatch.setattr(window.file_selector, "get_file", lambda: str(media))
    monkeypatch.setattr(window, "_ensure_whisper_model", lambda _model: False)
    window._start_transcription()
    assert window.transcript_view.get_result() is not None
    assert window._last_record_id == rid
    window.close()
    process_events()


def test_run_chip_only_says_running_for_the_run_s_own_record(env, monkeypatch, process_events):
    store, _ = env
    first = store.add(_result(), source_path="", source_name="a.mp3")
    second = store.add(_result(), source_path="", source_name="b.mp3")
    window = _window(process_events)
    window._open_record_view(second)
    seen = []
    monkeypatch.setattr(window.record_view, "set_run_summary",
                        lambda text, *a, **k: seen.append(text))
    monkeypatch.setattr(window.recipe_run, "is_running", lambda: True)
    window.recipe_run.record_id = first
    window._refresh_run_chip()
    assert seen and seen[-1] != tr("record_run_running")
    window.recipe_run.record_id = second
    window._refresh_run_chip()
    assert seen[-1] == tr("record_run_running")
    window.close()
    process_events()


def test_ok_does_not_close_settings_when_the_file_cannot_be_written(env, monkeypatch, process_events):
    from PyQt6.QtWidgets import QMessageBox

    from ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    dialog.show()
    applied = []
    dialog.settings_applied.connect(lambda: applied.append(1))
    warned = []
    monkeypatch.setattr("ui.settings_dialog.save_config", lambda: False)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[-1]))
    dialog._on_ok()
    assert dialog.isVisible() and not applied
    assert warned and str(config.CONFIG_FILE) in warned[0]
    dialog._on_apply()
    assert not applied
    monkeypatch.setattr("ui.settings_dialog.save_config", lambda: True)
    dialog._on_ok()
    assert applied and not dialog.isVisible()


def test_reset_ui_keeps_cancel_while_a_single_step_job_runs(env, monkeypatch, process_events):
    window = _window(process_events)
    window.cancel_btn.setVisible(True)
    window._reset_ui()
    assert not window.cancel_btn.isVisibleTo(window)
    running = type("Job", (), {"isRunning": lambda self: True})()
    monkeypatch.setattr(window, "_article_job", running)
    window._reset_ui()
    assert window.cancel_btn.isVisibleTo(window)
    monkeypatch.setattr(window, "_article_job", None)
    window.close()
    process_events()


def test_project_controller_leaves_another_operations_status_text(env, process_events):
    window = _window(process_events)
    window.status_bar.set_operation("Transcribing")
    window.project._status_text = "Exporting project"
    window.project._clear()
    assert window.status_bar.status_label.text() == "Transcribing"
    window.project._status_text = "Transcribing"
    window.project._clear()
    assert window.status_bar.status_label.text() != "Transcribing"
    window.close()
    process_events()


def test_unwired_photo_processing_settings_are_not_offered(env, process_events):
    from ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    dialog.show()
    process_events()
    for widget in (dialog._cover_provider_combo, dialog._cover_comfy_edit, dialog._cover_upscale_chk):
        assert not widget.isVisible() and widget.parent() is None
    assert dialog._cover_shorts_chk.parent() is not None            # the wired setting stays
    cfg = config.get_config()
    cfg.cover_image_provider = "comfyui"
    cfg.cover_comfy_url = "http://example:8188"
    dialog._load_values()
    dialog._save_values()                              # hidden, but saved values survive
    assert cfg.cover_comfy_url == "http://example:8188" and cfg.cover_image_provider == "comfyui"
    dialog.close()


def test_cancel_is_addressed_to_a_named_operation(env, monkeypatch, process_events):
    window = _window(process_events)
    stopped = []

    class Job:
        def __init__(self, name):
            self.name = name

        def isRunning(self):
            return self.name not in stopped

    monkeypatch.setattr(window, "_clean_job", Job("clean"))
    monkeypatch.setattr(window, "_book_job", Job("book"))
    monkeypatch.setattr(window.project, "is_busy", lambda: True)
    monkeypatch.setattr(window.project, "cancel", lambda: stopped.append("project"))
    monkeypatch.setattr(window, "_cancel_clean_job", lambda: stopped.append("clean"))
    monkeypatch.setattr(window, "_cancel_book_job", lambda: stopped.append("book"))
    keys = [op.key for op in window.operations.running()]
    assert keys == ["clean", "book", "project"]
    # The Cancel button asks which one when several are running.
    asked = []
    monkeypatch.setattr(window.operations, "pick", lambda ops: asked.append([o.key for o in ops]) or "book")
    window._cancel_operation()
    assert asked == [keys] and stopped == ["book"]
    # A panel's own Cancel stops only its operation, and the others stay cancellable.
    window._cancel_operation("clean")
    assert stopped == ["book", "clean"]
    assert [op.key for op in window.operations.running()] == ["project"]
    assert window.cancel_btn.isVisibleTo(window)               # the project export can still be cancelled
    monkeypatch.setattr(window.operations, "pick", lambda ops: "*")
    window._cancel_operation()
    assert "project" in stopped
    window.close()
    process_events()
