"""YouTubePanel as a pure step viewer (Y2b in
docs/UI_CONCEPT_IMPLEMENTATION_PLAN_2026-10.ru.md): no provider/language
controls of its own, one run link, and provider/language in Settings.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QComboBox

from transcriber import Segment, TranscriptionResult

_PAYLOAD = {
    "chapters": [{"start": 0, "title": "Intro"}],
    "yt_titles": ["A Great Title"],
    "yt_description": ["A hook."],
    "yt_tags": ["podcast"],
    "yt_questions": [],
}


def _panel():
    from ui.youtube_panel import YouTubePanel

    return YouTubePanel()


def test_panel_has_no_combo_boxes():
    panel = _panel()
    assert panel.findChildren(QComboBox) == []
    panel.close()


def test_state_row_follows_the_step():
    panel = _panel()
    assert not panel._state_bar.isVisibleTo(panel)
    assert panel._placeholder.isVisibleTo(panel)

    panel.set_segments([Segment(0.0, 1.0, "hi")], transcript_language="en")
    assert panel._state_bar.isVisibleTo(panel)
    assert panel._run_link.isVisibleTo(panel)
    assert not panel._placeholder.isVisibleTo(panel)

    panel.begin_generating()
    assert panel._state_bar.isVisibleTo(panel)
    assert not panel._run_link.isVisibleTo(panel)

    panel.set_error("LM Studio timed out")
    assert "LM Studio timed out" in panel._state_label.text()
    assert panel._run_link.isVisibleTo(panel)

    panel.begin_generating()
    panel.set_result(_PAYLOAD)
    assert not panel._state_bar.isVisibleTo(panel)

    # A transcript edit on the same record keeps the shown package.
    panel.set_segments([Segment(0.0, 1.0, "hi!")], transcript_language="en")
    assert not panel._state_bar.isVisibleTo(panel)

    panel.clear()
    assert panel._placeholder.isVisibleTo(panel)
    panel.close()


def test_run_link_asks_for_the_same_action():
    panel = _panel()
    panel.set_segments([Segment(0.0, 1.0, "hi")], transcript_language="en")
    fired = []
    panel.generate_requested.connect(lambda: fired.append(True))
    panel._run_link.click()
    assert fired == [True]
    panel.close()


def test_single_step_job_passes_the_configured_language(monkeypatch, process_events):
    seen = []

    def _fake(insight_type, segments, **kwargs):
        seen.append(kwargs.get("language"))
        return _PAYLOAD[insight_type]

    monkeypatch.setattr("core.insights.generate_insight", _fake)
    from config import get_config
    from ui.main_window import MainWindow

    cfg = get_config()
    original = cfg.yt_language
    cfg.yt_language = "English"
    try:
        window = MainWindow()
        cfg.lm_studio_url = "http://127.0.0.1:1234"
        cfg.yt_provider = "lmstudio"
        window._document_session.apply_result(TranscriptionResult(
            segments=[Segment(0.0, 1.0, "привет")], language="ru", duration=1.0,
        ))
        window._last_record_id = None
        window._source_filepath = None
        window.youtube_panel.generate_requested.emit()
        assert window._youtube_job.wait(2000)
        process_events()
        assert seen and set(seen) == {"English"}
        window.close()
    finally:
        cfg.yt_language = original


@pytest.fixture
def dialog(monkeypatch, tmp_path, process_events):
    import config
    from ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())

    dlg = SettingsDialog()
    yield dlg
    dlg.close()
    process_events()


def test_settings_load_and_save_provider_and_language(dialog, monkeypatch):
    import config

    cfg = config.Config(yt_provider="openai", yt_language="Russian")
    monkeypatch.setattr(config, "_config", cfg)
    dialog._cfg = cfg
    dialog._load_values()
    assert dialog._yt_provider_combo.currentData() == "openai"
    assert dialog._yt_language_combo.currentData() == "Russian"
    assert dialog._yt_configure_btn.isEnabled()
    assert not dialog._yt_privacy_notice.isHidden()

    dialog._yt_provider_combo.setCurrentIndex(dialog._yt_provider_combo.findData("lmstudio"))
    dialog._yt_language_combo.setCurrentIndex(dialog._yt_language_combo.findData(""))
    assert not dialog._yt_configure_btn.isEnabled()
    assert dialog._yt_privacy_notice.isHidden()
    dialog._save_values()
    assert (cfg.yt_provider, cfg.yt_language) == ("lmstudio", "")
