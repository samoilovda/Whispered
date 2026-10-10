"""The Cover workspace can be left and its cover saved to the record:
"Back" and "Save to record" are wired and "Save" needs a record."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def fresh_config(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())


def test_back_and_save_buttons_emit():
    from ui.cover_view import CoverView

    view = CoverView()
    seen: list[str] = []
    view.back_requested.connect(lambda: seen.append("back"))
    view.save_requested.connect(lambda: seen.append("save"))

    view.back_button.click()
    assert not view.inspector.save_button.isEnabled()   # no record yet
    view.set_provenance(7, None)
    assert view.inspector.save_button.isEnabled()
    view.inspector.save_button.click()

    assert seen == ["back", "save"]
    view.shutdown()
