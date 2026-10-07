"""Insights (I1, I2): section chips filter what is shown and copied;
tasks with a time get a link; empty sections say so."""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication, QPushButton

from core.i18n import load_locale, tr
from ui.insights_panel import InsightsPanel

PAYLOAD = {
    "chapters": [{"start": 0, "title": "Intro"}, {"start": 60, "title": "Plan"}],
    "action_items": [
        {"task": "Send the deck", "owner": "Ann", "deadline": None, "start": 75},
        {"task": "Book a room", "owner": None, "deadline": None},
    ],
    "key_moments": [],
}


def test_sections_show_counts_and_empty_notes(process_events):
    load_locale("en")
    panel = InsightsPanel()
    panel.show()
    panel.set_result(PAYLOAD)
    process_events()
    assert panel._ai_header.text() == f"{tr('insights_action_items')} · 2"
    assert panel._km_empty.isVisible()
    assert panel._km_empty.text() == tr("insights_none_key_moments")
    links = [b for b in panel._ai_container.findChildren(QPushButton) if b.text() == "01:15"]
    assert len(links) == 1  # only the task that had a time
    seeks = []
    panel.seek_requested.connect(seeks.append)
    links[0].click()
    assert seeks == [75]


def test_chips_filter_what_is_shown_and_copied(process_events):
    load_locale("en")
    panel = InsightsPanel()
    panel.show()
    panel.set_result(PAYLOAD)
    process_events()
    panel._chip_buttons["chapters"].setChecked(False)
    process_events()
    assert not panel._ch_container.isVisible()
    panel._copy_visible()
    copied = QApplication.clipboard().text()
    assert "Send the deck" in copied and "Intro" not in copied


def test_not_generated_sections_stay_hidden(process_events):
    load_locale("en")
    panel = InsightsPanel()
    panel.show()
    panel.set_shared_chapters([{"start": 0, "title": "From YouTube"}])
    process_events()
    assert panel._ch_container.isVisible()
    assert not panel._ai_header.isVisible()
    assert not panel._chip_buttons["action_items"].isVisible()
