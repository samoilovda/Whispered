"""Y4b (docs/archive/UI_CONCEPT_IMPLEMENTATION_PLAN_2026-10.ru.md): pick the title
to publish, see title and tag lengths against YouTube's limits, and copy
what the open section is for.
"""

from __future__ import annotations

import json

from PyQt6.QtWidgets import QApplication, QLabel, QRadioButton

from transcriber import Segment

_LONG = "A title that is clearly longer than what search results show to most viewers"
_PAYLOAD = {
    "chapters": [{"start": 0, "title": "Intro"}, {"start": 60, "title": "Body"}],
    "yt_titles": ["1. First", "«Second»", _LONG, "First"],
    "yt_description": ["Hook."],
    "yt_tags": ["podcast", "local ai"],
    "yt_questions": [{"start": 5, "title": "Why?"}],
}


def _panel():
    from ui.youtube_panel import YouTubePanel

    panel = YouTubePanel()
    panel.set_provenance(21, "/media/talk.mp4")
    panel.set_segments([Segment(0.0, 600.0, "hello")], transcript_language="en")
    panel.begin_generating()
    panel.set_result(_PAYLOAD)
    return panel


def _radios(panel) -> list[QRadioButton]:
    return panel._title_rows.findChildren(QRadioButton)


def test_titles_are_cleaned_like_the_publish_dialog_and_first_is_chosen():
    panel = _panel()
    radios = _radios(panel)
    assert [r.accessibleName() for r in radios] == ["First", "Second", _LONG]
    assert radios[0].isChecked()
    assert panel._titles_edit.toPlainText().startswith("1. 1. First")   # Save keeps the raw list
    assert not panel._titles_edit.isVisibleTo(panel)
    panel.close()


def test_picking_a_title_puts_it_first_for_publishing_and_is_remembered():
    panel = _panel()
    _radios(panel)[1].click()
    assert panel.publish_texts()["titles"] == ["Second", "First", _LONG]
    saved = json.loads(panel._overlay_file().read_text(encoding="utf-8"))
    assert saved["title"] == "Second"

    again = _panel()
    assert _radios(again)[1].isChecked()
    panel.close()
    again.close()


def test_picking_the_first_again_records_no_edit():
    panel = _panel()
    _radios(panel)[1].click()
    _radios(panel)[0].click()
    assert not panel._overlay_file().exists()
    panel.close()


def test_clicking_the_title_text_picks_it():
    panel = _panel()
    from ui.youtube_panel import _TitleLabel

    label = [w for w in panel._title_rows.findChildren(_TitleLabel) if w.text() == "Second"][0]
    label.clicked.emit()
    assert _radios(panel)[1].isChecked()
    panel.close()


def test_a_regenerated_list_without_the_chosen_title_falls_back_to_the_first():
    panel = _panel()
    _radios(panel)[1].click()
    panel.begin_generating()
    panel.set_result(dict(_PAYLOAD, yt_titles=["Brand new", "Another"]))
    assert panel.publish_texts()["titles"][0] == "Brand new"
    panel.close()


def test_title_lengths_are_flagged():
    panel = _panel()
    counts = {
        row.findChild(QRadioButton).accessibleName(): [
            lbl for lbl in row.findChildren(QLabel) if "/100" in lbl.text()
        ][0]
        for row in [r.parentWidget() for r in _radios(panel)]
    }
    assert counts["First"].property("role") == "muted"
    assert counts[_LONG].property("role") == "warning-text"
    panel.close()


def test_tags_size_is_counted_like_the_upload():
    panel = _panel()
    # "podcast"(7) + "local ai" quoted (8+2) + one separator = 18
    assert panel._tags_size.text().startswith("18 / 500")
    panel._tags_edit.setPlainText(", ".join(f"tag{i:03d}" for i in range(80)))
    assert panel._tags_size.property("role") == "warning-text"
    panel.close()


def test_copy_takes_what_the_open_section_is_for():
    panel = _panel()
    clipboard = QApplication.clipboard()

    panel._tabs.setCurrentIndex(1)            # titles
    _radios(panel)[1].click()
    assert "title" in panel._copy_btn.text().lower() or "назв" in panel._copy_btn.text().lower()
    panel._copy_btn.click()
    assert clipboard.text() == "Second"

    panel._tabs.setCurrentIndex(2)            # description
    panel._copy_btn.click()
    assert clipboard.text() == panel._desc_edit.toPlainText()

    panel._tabs.setCurrentIndex(0)            # chapters → timecodes
    panel._copy_btn.click()
    assert clipboard.text().startswith("0:00 Intro")
    panel.close()


def test_the_chosen_title_is_bold():
    from ui.youtube_panel import _TitleLabel

    panel = _panel()
    _radios(panel)[1].click()
    weights = {
        label.text(): "600" in label.styleSheet()
        for label in panel._title_rows.findChildren(_TitleLabel)
    }
    assert weights == {"First": False, "Second": True, _LONG: False}
    panel.close()
