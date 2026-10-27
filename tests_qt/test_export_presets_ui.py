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
        "application.export_plan.execute_export_plan",
        lambda *a, **k: called.append(1),
    )

    window._export_preset("youtube")
    process_events()

    assert called == []


def test_export_preset_with_an_unknown_key_is_a_noop(window, process_events, monkeypatch):
    window.transcript_view.set_result(_result())
    called = []
    monkeypatch.setattr(
        "application.export_plan.execute_export_plan",
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
        "application.export_plan.execute_export_plan",
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
    # UX-15: plan → review → execute; the review is accepted here.
    monkeypatch.setattr(window, "_review_export_plan", lambda plan: (True, False))
    monkeypatch.setattr("application.export_plan.execute_export_plan", lambda *a, **k: None)
    monkeypatch.setattr(
        "application.export_controller.outcome_from_report", lambda *a, **k: outcome,
    )

    seen_toasts = []
    monkeypatch.setattr("ui.main_window.show_toast", lambda *a, **k: seen_toasts.append(k))

    window._export_preset("article_draft")
    _until(lambda: seen_toasts)

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
    # UX-15: plan → review → execute; the review is accepted here.
    monkeypatch.setattr(window, "_review_export_plan", lambda plan: (True, False))
    monkeypatch.setattr("application.export_plan.execute_export_plan", lambda *a, **k: None)
    monkeypatch.setattr(
        "application.export_controller.outcome_from_report", lambda *a, **k: outcome,
    )

    seen_toasts = []
    monkeypatch.setattr("ui.main_window.show_toast", lambda *a, **k: seen_toasts.append(k))

    window._export_preset("youtube")
    _until(lambda: seen_toasts)

    assert seen_toasts == [{"kind": "warning"}]


def test_declined_review_writes_nothing(window, process_events, monkeypatch, tmp_path):
    """UX-15: the bundle is shown first; Cancel means nothing is written."""
    from PyQt6.QtWidgets import QFileDialog

    window.transcript_view.set_result(_result())
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(out_dir)),
    )
    reviewed = []
    monkeypatch.setattr(window, "_review_export_plan",
                        lambda plan: reviewed.append(plan) or (False, False))
    window._export_preset("article_draft")
    _until(lambda: reviewed)
    assert reviewed and [i.key for i in reviewed[0].items][:2] == ["md", "docx"]
    assert list(out_dir.iterdir()) == []


def _until(condition, timeout_ms: int = 5000) -> None:
    """Planning and writing run on worker threads: let their signals arrive."""
    from PyQt6.QtTest import QTest

    waited = 0
    while not condition() and waited < timeout_ms:
        QTest.qWait(20)
        waited += 20


def test_plan_dialog_lists_freshness_and_offers_overwrite(process_events):
    from application.export_plan import CURRENT, MISSING, STALE, ExportItem, ExportPlan
    from core.i18n import load_locale
    from ui.export_plan_dialog import ExportPlanDialog

    load_locale("en")
    plan = ExportPlan(1, "r", "/tmp/out", "src", (
        ExportItem("format", "srt", "src.srt", exists=True),
        ExportItem("material", "book", "book.md", "/a/book.md", STALE),
        ExportItem("material", "cover", "cover.png", freshness=MISSING),
        ExportItem("material", "article", "articles.json", "/a/articles.json", CURRENT,
                   user_edits=("title",), overlay="/a/articles.user.json"),
    ))
    dialog = ExportPlanDialog(plan)
    texts = [dialog.items.item(i).text() for i in range(dialog.items.count())]
    assert texts[0].startswith("✓ src.srt") and tr("export_plan_exists") in texts[0]
    assert texts[1].startswith("◐ book.md")
    assert texts[2].startswith("—")
    assert tr("export_plan_edits", keys="title") in texts[3]
    assert not dialog.overwrite.isHidden() and not dialog.overwrite.isChecked()
    dialog.close()


def test_bundle_planning_and_writing_leave_the_gui_thread(window, process_events, monkeypatch, tmp_path):
    import threading

    from PyQt6.QtWidgets import QFileDialog
    from application import export_plan

    window.transcript_view.set_result(_result())
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(out_dir)))
    monkeypatch.setattr(window, "_review_export_plan", lambda plan: (True, False))
    threads = {}
    real_build, real_execute = export_plan.build_export_plan, export_plan.execute_export_plan
    monkeypatch.setattr(export_plan, "build_export_plan",
                        lambda *a, **k: threads.setdefault("build", threading.current_thread()) and real_build(*a, **k))
    monkeypatch.setattr(export_plan, "execute_export_plan",
                        lambda *a, **k: threads.setdefault("write", threading.current_thread()) and real_execute(*a, **k))
    toasts = []
    monkeypatch.setattr("ui.main_window.show_toast", lambda *a, **k: toasts.append(k))
    window._export_preset("article_draft")
    _until(lambda: toasts)
    assert toasts and threads["build"] is not threading.main_thread()
    assert threads["write"] is not threading.main_thread()
    assert window.status_bar.status_label.text() == tr("status_idle")


def test_reindexing_materials_runs_in_the_background(process_events, monkeypatch):
    import threading

    from PyQt6.QtWidgets import QMessageBox
    from ui.settings_dialog import SettingsDialog

    seen = {}

    def fake(is_cancelled=None):
        seen["thread"] = threading.current_thread()
        return 7

    monkeypatch.setattr("application.steps.reindex_artifacts", fake)
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a[-1]))
    dialog = SettingsDialog()
    dialog._reindex_materials()
    assert not dialog._reindex_btn.isEnabled() and dialog._reindex is not None
    dialog._reindex_materials()                                # a second click is ignored
    _until(lambda: infos)
    assert infos and "7" in infos[0] and seen["thread"] is not threading.main_thread()
    assert dialog._reindex_btn.isEnabled() and dialog._reindex is None
    dialog.close()
