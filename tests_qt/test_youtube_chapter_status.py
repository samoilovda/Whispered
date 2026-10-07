"""YouTubePanel's chapter status line: what check_chapters() found is shown
next to the chapters instead of only being logged. Real Qt because the
panel is a QWidget and the role/visibility are what the user sees.
"""

from __future__ import annotations

from transcriber import Segment

_GOOD = [
    {"start": 0, "title": "Intro"},
    {"start": 60, "title": "Body"},
    {"start": 120, "title": "Outro"},
]


def _panel(duration: float = 300.0):
    from ui.youtube_panel import YouTubePanel

    panel = YouTubePanel()
    panel.set_segments([Segment(0.0, duration, "hello")], transcript_language="en")
    return panel


def test_hidden_until_chapters_arrive():
    panel = _panel()
    assert not panel._chapter_status.isVisibleTo(panel)
    panel.close()


def test_clean_chapters_show_success():
    panel = _panel()
    panel.set_result({"chapters": _GOOD})
    assert panel._chapter_status.isVisibleTo(panel)
    assert panel._chapter_status.property("role") == "success-text"
    assert panel._chapter_status.text().count("\n") == 0
    panel.close()


def test_too_few_and_dropped_chapters_are_explained():
    panel = _panel()
    panel.set_result({"chapters": [
        {"start": 0, "title": "Intro"},
        {"start": 4, "title": "Squeezed"},
    ]})
    text = panel._chapter_status.text()
    assert panel._chapter_status.property("role") == "danger-text"
    assert "Squeezed" in text
    assert "0:04" in text
    panel.close()


def test_past_end_uses_transcript_duration():
    panel = _panel(duration=100.0)
    panel.set_result({"chapters": _GOOD})
    assert panel._chapter_status.property("role") == "warning-text"
    assert "Outro" in panel._chapter_status.text()
    panel.close()


def test_status_cleared_by_new_run_and_error():
    panel = _panel()
    panel.set_result({"chapters": _GOOD})
    panel.begin_generating()
    assert not panel._chapter_status.isVisibleTo(panel)
    panel.set_result({"chapters": _GOOD})
    panel.set_error("LM Studio timed out")
    assert not panel._chapter_status.isVisibleTo(panel)
    panel.close()


def test_status_follows_language_switch():
    from core.i18n import current_lang, set_locale

    panel = _panel()
    panel.set_result({"chapters": [{"start": 0, "title": "Only"}]})
    original = current_lang()
    try:
        set_locale("en")
        english = panel._chapter_status.text()
        set_locale("ru")
        russian = panel._chapter_status.text()
    finally:
        set_locale(original)
    assert english != russian
    assert "YouTube" in english and "YouTube" in russian
    panel.close()
