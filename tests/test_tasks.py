"""domain.tasks, application.task_export and task_store (N04)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from application import task_export, task_store
from domain import tasks as t
from domain.transcription import Segment

SEGS = [Segment(0.0, 20.0, "We agreed Alice sends the proposal."),
        Segment(20.0, 40.0, "Budget review is next week.")]
ITEMS = [{"task": "Send the proposal", "owner": "Alice", "deadline": "Friday", "start": 5},
         {"task": "Review budget", "owner": None, "deadline": None, "start": None},
         {"task": "Ghost task", "owner": "Bob", "deadline": None, "start": 9999}]


def test_proposals_become_cards_with_verified_sources_and_no_inventions():
    cards = t.merge(ITEMS, [], SEGS)
    send, review, ghost = cards
    assert (send.status, send.owner, send.deadline) == (t.PROPOSED, "Alice", "Friday")
    assert send.quote == "We agreed Alice sends the proposal."
    assert review.owner is None and review.deadline is None and review.start is None
    assert review.quote == ""                      # no time → no source claimed
    assert ghost.start == 9999 and ghost.quote == ""   # a time outside the recording is unverified


def test_user_work_survives_a_regeneration_and_conflicts_are_visible():
    first = t.merge(ITEMS, [], SEGS)
    first[0].status, first[0].deadline, first[0].edited = t.ACCEPTED, "2026-11-06", True
    stored = t.tasks_from_dict(t.tasks_to_dict(first))
    regenerated = [{"task": "Review budget", "owner": "Carol", "deadline": None, "start": 22},
                   {"task": "Book a room", "owner": None, "deadline": None, "start": None}]
    cards = t.merge(regenerated, stored, SEGS)
    by_task = {c.task: c for c in cards}
    assert by_task["Send the proposal"].status == t.ACCEPTED       # kept, though the model dropped it
    assert by_task["Send the proposal"].in_result is False
    assert by_task["Send the proposal"].deadline == "2026-11-06"
    assert by_task["Review budget"].owner == "Carol" and by_task["Review budget"].in_result
    assert "Book a room" in by_task
    assert "Ghost task" not in by_task                              # untouched + gone: dropped


def test_hand_typed_tasks_say_they_have_no_source():
    card = t.new_user_card("Call the client")
    assert card.origin == t.USER and not card.has_source and card.owner is None
    assert t.new_user_card("x", [card.id]).id != card.id
    assert t.merge([], [card])[0] is card


def test_deadlines_are_read_only_when_written_as_dates():
    assert t.parse_deadline("2026-11-03") == date(2026, 11, 3)
    assert t.parse_deadline("2026-11-03 15:30") == datetime(2026, 11, 3, 15, 30)
    assert t.parse_deadline("03.11.2026") == date(2026, 11, 3)
    for text in ("Friday", "next week", "ASAP", "2026-02-30", "", None, "2026-13-01"):
        assert t.parse_deadline(text) is None


def test_markdown_and_csv_keep_unknowns_empty():
    cards = t.merge(ITEMS[:2], [], SEGS)
    cards[0].status = t.DONE
    md = task_export.to_markdown(cards)
    assert "- [x] Send the proposal (owner: Alice; due: Friday; at 0:05)" in md
    assert "- [ ] Review budget (owner: —; due: —)" in md
    csv_text = task_export.to_csv(cards)
    assert csv_text.splitlines()[0] == ",".join(task_export.CSV_COLUMNS)
    assert csv_text.splitlines()[2].startswith("Review budget,,,proposed,,,model")
    cards[1].status = t.DISMISSED
    assert "Review budget" not in task_export.to_markdown(cards)


def test_ics_only_for_accepted_tasks_with_a_real_date_and_is_well_formed():
    now = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
    cards = t.merge(ITEMS[:2], [], SEGS)
    assert task_export.to_ics(cards, now) == ""            # nothing accepted / no real date
    cards[0].status, cards[0].deadline = t.ACCEPTED, "Friday"
    assert task_export.to_ics(cards, now) == ""            # "Friday" is not a date
    cards[0].deadline = "2026-11-06"
    cards[0].task = "Send; the proposal, now\nplease " + "x" * 100
    ics = task_export.to_ics(cards, now)
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "DUE;VALUE=DATE:20261106" in ics and "DTSTAMP:20261018T090000Z" in ics
    assert "Send\\; the proposal\\, now\\nplease" in ics.replace("\r\n ", "")
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    cards[0].deadline = "2026-11-06 15:30"
    assert "DUE:20261106T153000" in task_export.to_ics(cards, now)
    cards[0].status = t.DONE
    assert task_export.to_ics(cards, now) == ""


def test_store_keeps_other_overlay_keys_and_a_newer_schema(tmp_path):
    from application.user_edits import load_overlay, save_overlay

    save_overlay(task_store.overlay_file(tmp_path), {"title": "kept"})
    card = t.new_user_card("Mine")
    task_store.save_cards(tmp_path, [card])
    assert load_overlay(task_store.overlay_file(tmp_path))["title"] == "kept"
    assert [c.task for c in task_store.load_cards(tmp_path)] == ["Mine"]
    task_store.save_cards(tmp_path, [])
    assert task_store.load_cards(tmp_path) == []
    save_overlay(task_store.overlay_file(tmp_path), {"tasks": {"schema_version": 99, "cards": []}})
    assert task_store.load_cards(tmp_path) is None


def test_csv_export_cannot_smuggle_spreadsheet_formulas():
    import csv
    import io

    cards = [t.new_user_card("=1+1", []), t.new_user_card("  +SUM(A1)", ["x"]),
             t.new_user_card("Заказать кофе\nи чай", ["y"])]
    cards[0].owner, cards[0].deadline = "@cmd", "-5"
    cards[2].owner = "Алиса"
    rows = list(csv.reader(io.StringIO(task_export.to_csv(cards), newline="")))
    assert rows[1][0] == "'=1+1" and rows[1][1] == "'@cmd" and rows[1][2] == "'-5"
    assert rows[2][0] == "'+SUM(A1)"
    assert rows[3][0] == "Заказать кофе\nи чай" and rows[3][1] == "Алиса"     # normal text untouched
