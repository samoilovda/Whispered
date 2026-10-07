"""Y4a (docs/UI_CONCEPT_IMPLEMENTATION_PLAN_2026-10.ru.md): the YouTube
description is assembled from blocks the user picks; the choice is kept
in the user-edit overlay; size is checked against YouTube's limit.
"""

from __future__ import annotations

import json

import pytest

from transcriber import Segment

_PAYLOAD = {
    "chapters": [
        {"start": 0, "title": "Intro"}, {"start": 60, "title": "Body"}, {"start": 120, "title": "End"},
    ],
    "yt_titles": ["T"],
    "yt_description": ["Hook."],
    "yt_tags": ["a"],
    "yt_questions": [{"start": 65, "title": "Why?"}],
}


@pytest.fixture
def signature():
    from config import get_config

    cfg = get_config()
    original = cfg.yt_channel_signature
    cfg.yt_channel_signature = "Subscribe: [link]"
    yield cfg
    cfg.yt_channel_signature = original


def _panel(payload=None):
    from ui.youtube_panel import YouTubePanel

    panel = YouTubePanel()
    panel.set_provenance(11, "/media/talk.mp4")
    panel.set_segments([Segment(0.0, 600.0, "hello")], transcript_language="en")
    panel.begin_generating()
    panel.set_result(payload or _PAYLOAD)
    return panel


def test_default_blocks_are_text_timecodes_and_signature(signature):
    panel = _panel()
    text = panel._desc_edit.toPlainText()
    assert text.startswith("Hook.")
    assert "0:00 Intro" in text
    assert text.endswith("Subscribe: [link]")
    assert "Why?" not in text
    assert panel._desc_block_btns["text"].isChecked()
    assert not panel._desc_block_btns["questions"].isChecked()
    panel.close()


def test_toggling_a_block_recomposes_and_is_remembered(signature):
    panel = _panel()
    panel._desc_block_btns["questions"].click()
    panel._desc_block_btns["signature"].click()
    text = panel._desc_edit.toPlainText()
    assert "1:05 Why?" in text
    assert "Subscribe" not in text
    assert panel.publish_texts()["description"] == text

    saved = json.loads(panel._overlay_file().read_text(encoding="utf-8"))
    assert saved["description_blocks"] == ["text", "timecodes", "questions"]

    again = _panel()                      # same record: choice comes back
    assert again._desc_edit.toPlainText() == text
    panel.close()
    again.close()


def test_back_to_the_default_records_no_edit(signature):
    panel = _panel()
    panel._desc_block_btns["questions"].click()
    panel._desc_block_btns["questions"].click()
    assert not panel._overlay_file().exists()
    panel.close()


def test_signature_chip_is_disabled_without_a_signature():
    from config import get_config

    assert not get_config().yt_channel_signature
    panel = _panel()
    assert not panel._desc_block_btns["signature"].isEnabled()
    assert panel._desc_block_btns["signature"].toolTip()
    panel.close()


def test_refresh_picks_up_a_new_signature(signature):
    panel = _panel()
    signature.yt_channel_signature = "New footer"
    panel.refresh_description()
    assert panel._desc_edit.toPlainText().endswith("New footer")
    panel.close()


def test_chapter_edit_flows_into_the_description(signature):
    panel = _panel()
    panel._chapters_edit_btn.click()
    panel._chapters_edit.setPlainText("0:00 Hello\n1:00 Body\n2:00 End")
    panel._chapters_done_btn.click()
    assert "0:00 Hello" in panel._desc_edit.toPlainText()
    assert panel._desc_edit.toPlainText().endswith("Subscribe: [link]")
    panel.close()


def test_size_and_fold_lines():
    panel = _panel()
    assert "/ 5000" in panel._desc_size.text()
    assert panel._desc_size.property("role") == "muted"
    assert "Hook." in panel._desc_fold.text()

    long = dict(_PAYLOAD, yt_description=["Ж" * 2600])   # 5200 bytes in UTF-8
    over = _panel(long)
    assert over._desc_size.property("role") == "warning-text"
    panel.close()
    over.close()


def test_settings_saves_the_signature(monkeypatch, tmp_path, process_events):
    import config
    from ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    cfg = config.Config(yt_channel_signature="Old")
    monkeypatch.setattr(config, "_config", cfg)
    dialog = SettingsDialog()
    dialog._cfg = cfg
    dialog._load_values()
    assert dialog._yt_signature_edit.toPlainText() == "Old"
    dialog._yt_signature_edit.setPlainText("  Links\nBye  ")
    dialog._save_values()
    assert cfg.yt_channel_signature == "Links\nBye"
    dialog.close()
    process_events()
