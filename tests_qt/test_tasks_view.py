"""Task cards in the Insights tab (N04)."""

from __future__ import annotations

import pytest

import config
from core.i18n import load_locale, tr
from domain import tasks as t
from transcriber import Segment

SEGS = [Segment(0.0, 20.0, "Alice will send the proposal."), Segment(20.0, 40.0, "Budget next week.")]
RESULT = {"chapters": [], "key_moments": [], "action_items": [
    {"task": "Send the proposal", "owner": "Alice", "deadline": "Friday", "start": 5},
    {"task": "Review budget", "owner": None, "deadline": None, "start": None},
]}


@pytest.fixture
def panel(monkeypatch, tmp_path, process_events):
    from ui.insights_panel import InsightsPanel

    load_locale("en")
    monkeypatch.setattr(config, "_config", config.Config())
    monkeypatch.setattr("core.paths.artifact_dir", lambda record_id, source: tmp_path / f"a-{record_id}")
    created = []

    def make(record_id=7):
        view = InsightsPanel()
        view.set_segments(SEGS, "en")
        view.set_provenance(record_id, "/m/talk.mp3")
        view.show()
        created.append(view)
        return view

    return make, tmp_path


def _rows(panel):
    from ui.tasks_view import _TaskRow

    return panel._tasks_view.findChildren(_TaskRow)


def test_proposals_show_unknowns_as_unknown_and_sources_as_sources(panel, process_events):
    make, _ = panel
    view = make()
    view.set_result(RESULT)
    first, second = _rows(view)
    assert first.owner.text() == "Alice" and first.deadline.text() == "Friday"
    assert "Alice will send the proposal." in first.note.text()
    assert tr("task_deadline_not_a_date") in first.note.text()
    assert second.owner.text() == "" and second.owner.placeholderText() == tr("task_owner_unknown")
    assert tr("task_no_source") in second.note.text()
    assert first.seek_btn is not None and second.seek_btn is None


def test_decisions_are_stored_and_survive_a_regeneration_and_a_reopen(panel, process_events):
    make, tmp_path = panel
    view = make()
    view.set_result(RESULT)
    first = _rows(view)[0]
    first.status.setCurrentIndex(first.status.findData(t.ACCEPTED))
    first.deadline.setText("2026-11-06")
    first.deadline.editingFinished.emit()
    assert first.card.edited and first.card.status == t.ACCEPTED
    view._tasks_view.new_task.setText("Call the client")
    view._tasks_view._add_task()
    assert any(r.card.origin == t.USER and tr("task_no_source_user") in r.note.text()
               for r in _rows(view))

    view.set_result({**RESULT, "action_items": [RESULT["action_items"][1]]})   # regenerated: first is gone
    cards = {r.card.task: r.card for r in _rows(view)}
    assert cards["Send the proposal"].status == t.ACCEPTED and not cards["Send the proposal"].in_result
    assert tr("task_not_in_result") in [r for r in _rows(view) if r.card.task == "Send the proposal"][0].note.text()

    reopened = make()          # a new panel for the same record reads the stored cards
    reopened.set_result(RESULT)
    again = {r.card.task: r.card for r in _rows(reopened)}
    assert again["Send the proposal"].deadline == "2026-11-06"
    assert "Call the client" in again


def test_unsaved_record_keeps_nothing_on_disk(panel, process_events):
    make, tmp_path = panel
    view = make(record_id=None)
    view.set_result(RESULT)
    _rows(view)[0].status.setCurrentIndex(1)
    assert not list(tmp_path.rglob("insights.user.json"))


def test_export_menu_follows_what_can_be_exported(panel, process_events):
    make, _ = panel
    view = make()
    tasks = view._tasks_view
    assert not tasks.export_btn.isEnabled()
    view.set_result(RESULT)
    assert tasks.export_btn.isEnabled() and not tasks._ics_action.isEnabled()
    first = _rows(view)[0]
    first.status.setCurrentIndex(first.status.findData(t.ACCEPTED))
    first.deadline.setText("2026-11-06")
    first.deadline.editingFinished.emit()
    assert tasks._ics_action.isEnabled()
    assert "BEGIN:VTODO" in tasks.export_text("ics")
    assert "[ ] Send the proposal" in tasks.export_text("md")
