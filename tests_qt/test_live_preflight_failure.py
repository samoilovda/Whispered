"""A crashing Live readiness check must not leave the screen stuck."""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel

import config
from core.history import HistoryStore
from core.i18n import load_locale


def test_failed_preflight_recovers_the_screen(monkeypatch, tmp_path, process_events):
    load_locale("en")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)

    def boom(self, **kwargs):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr("core.live.preflight.LivePreflight.run", boom)
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()
    view = window.live_view
    window.live_session.run_preflight()
    worker = window.live_session._preflight_worker
    assert worker is not None and worker.wait(5000)
    process_events()
    assert view.preflight_btn.isEnabled()
    assert not view.start_btn.isEnabled()
    labels = " ".join(label.text() for label in view.preflight_panel.findChildren(QLabel))
    assert "probe exploded" in labels
    window.close()
    process_events()
