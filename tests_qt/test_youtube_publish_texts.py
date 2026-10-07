"""YouTubePanel.publish_texts(): the publish dialog must see what the user
sees in the tabs (edits included), not what the step wrote to disk."""

from __future__ import annotations

from transcriber import Segment

_PAYLOAD = {
    "chapters": [
        {"start": 0, "title": "Intro"},
        {"start": 60, "title": "Body"},
        {"start": 120, "title": "Outro"},
    ],
    "yt_titles": ["First", "Second"],
    "yt_description": ["Hook and summary"],
    "yt_tags": ["a", "b"],
}


def _panel():
    from ui.youtube_panel import YouTubePanel

    panel = YouTubePanel()
    panel.set_segments([Segment(0.0, 300.0, "hello")], transcript_language="en")
    return panel


def test_empty_panel_has_nothing_to_publish():
    panel = _panel()
    texts = panel.publish_texts()
    assert texts["titles"] == [] and texts["description"] == ""
    assert not panel.has_publishable_content()
    panel.close()


def test_texts_reflect_the_generated_result_with_timecodes():
    panel = _panel()
    panel.set_result(_PAYLOAD)
    texts = panel.publish_texts()
    assert panel.has_publishable_content()
    assert texts["titles"][0].endswith("First")
    assert texts["description"].startswith("Hook and summary")
    assert "0:00" in texts["description"] and "Outro" in texts["description"]
    assert texts["tags"] == "a, b"
    assert texts["language"] == "en"
    assert texts["chapter_check"] is not None
    panel.close()


def test_user_edits_are_what_gets_published():
    panel = _panel()
    panel.set_result(_PAYLOAD)
    panel._desc_edit.setPlainText("Edited by hand")
    panel._tags_edit.setPlainText("x, y, z")
    texts = panel.publish_texts()
    assert texts["description"] == "Edited by hand"
    assert texts["tags"] == "x, y, z"
    panel.close()


def test_language_is_a_code_even_when_the_combo_names_a_language():
    panel = _panel()
    panel.set_result(_PAYLOAD)
    panel._lang_combo.setCurrentIndex(panel._lang_combo.findData("Russian"))
    assert panel.publish_texts()["language"] == "ru"
    panel._lang_combo.setCurrentIndex(0)
    assert panel.publish_texts()["language"] == "en"     # falls back to the transcript's
    panel.close()
