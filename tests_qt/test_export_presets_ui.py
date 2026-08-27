"""Real-Qt tests for B9's UI wiring (docs/IMPROVEMENT_PLAN_2026-08.ru.md):
RecordView's export menu listing presets before formats, and MainWindow
routing a chosen preset through application/export_controller.py — the
Qt-free collection logic itself is covered by tests/test_export_controller.py.
"""

from __future__ import annotations

import pytest

from core.i18n import load_locale, tr
from domain.export_preset import BUILTIN_EXPORT_PRESETS
from transcriber import Segment, TranscriptionResult
from ui.record_view import RecordView


def _result():
    return TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello world", speaker=None)], language="en", duration=1.0,
    )


# ------------------------------------------------------------------ RecordView menu

def test_export_menu_lists_every_builtin_preset(process_events):
    load_locale("en")
    view = RecordView()
    process_events()

    menu = view.export_btn.menu()
    labels = [act.text() for act in menu.actions()]
    for preset in BUILTIN_EXPORT_PRESETS:
        assert tr(f"export_preset_{preset.key}") in labels

    view.close()


def test_preset_actions_come_before_the_format_separator(process_events):
    """B9 item 3: presets first, formats below a separator."""
    view = RecordView()
    process_events()

    menu = view.export_btn.menu()
    actions = menu.actions()
    separator_index = next(i for i, act in enumerate(actions) if act.isSeparator())
    preset_count = len(BUILTIN_EXPORT_PRESETS)

    assert separator_index == preset_count
    for act in actions[:preset_count]:
        assert not act.isCheckable()

    view.close()


def test_clicking_a_preset_action_emits_its_key(process_events):
    view = RecordView()
    process_events()

    seen = []
    view.export_preset_requested.connect(seen.append)

    menu = view.export_btn.menu()
    youtube_action = next(
        act for act in menu.actions()
        if act.text() == tr("export_preset_youtube")
    )
    youtube_action.trigger()
    process_events()

    assert seen == ["youtube"]

    view.close()


# ------------------------------------------------------------------ MainWindow routing

@pytest.fixture
def window(monkeypatch, tmp_path, process_events):
    import config
    from core.history import HistoryStore
    from ui.main_window import MainWindow

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())

    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    win = MainWindow()
    yield win
    win.close()
    process_events()


def test_export_preset_with_no_result_loaded_is_a_noop(window, process_events, monkeypatch):
    called = []
    monkeypatch.setattr(
        "application.export_controller.export_preset",
        lambda *a, **k: called.append(1),
    )

    window._export_preset("youtube")
    process_events()

    assert called == []


def test_export_preset_with_an_unknown_key_is_a_noop(window, process_events, monkeypatch):
    window.transcript_view.set_result(_result())
    called = []
    monkeypatch.setattr(
        "application.export_controller.export_preset",
        lambda *a, **k: called.append(1),
    )

    window._export_preset("not-a-real-preset")
    process_events()

    assert called == []


def test_export_preset_with_no_directory_chosen_does_not_call_the_controller(
    window, process_events, monkeypatch,
):
    from PyQt6.QtWidgets import QFileDialog

    window.transcript_view.set_result(_result())
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))
    called = []
    monkeypatch.setattr(
        "application.export_controller.export_preset",
        lambda *a, **k: called.append(1),
    )

    window._export_preset("youtube")
    process_events()

    assert called == []


def test_export_preset_shows_a_success_toast_when_nothing_is_missing(
    window, process_events, monkeypatch, tmp_path,
):
    from PyQt6.QtWidgets import QFileDialog
    from application import export_controller

    window.transcript_view.set_result(_result())
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(out_dir)),
    )

    outcome = export_controller.PresetExportOutcome()
    outcome.materials_copied = ["article"]
    outcome.formats.succeeded = ["md", "docx"]
    monkeypatch.setattr(
        "application.export_controller.export_preset", lambda *a, **k: outcome,
    )

    seen_toasts = []
    monkeypatch.setattr("ui.main_window.show_toast", lambda *a, **k: seen_toasts.append(k))

    window._export_preset("article_draft")
    process_events()

    assert seen_toasts == [{"kind": "success"}]


def test_export_preset_shows_a_warning_toast_when_something_is_missing(
    window, process_events, monkeypatch, tmp_path,
):
    from PyQt6.QtWidgets import QFileDialog
    from application import export_controller

    window.transcript_view.set_result(_result())
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(out_dir)),
    )

    outcome = export_controller.PresetExportOutcome()
    outcome.materials_missing = ["youtube_package", "cover"]
    outcome.formats.succeeded = ["srt", "vtt"]
    monkeypatch.setattr(
        "application.export_controller.export_preset", lambda *a, **k: outcome,
    )

    seen_toasts = []
    monkeypatch.setattr("ui.main_window.show_toast", lambda *a, **k: seen_toasts.append(k))

    window._export_preset("youtube")
    process_events()

    assert seen_toasts == [{"kind": "warning"}]
