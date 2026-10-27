"""application/operations.py — Cancel addresses a named operation."""

from __future__ import annotations

from application.operations import Operation, cancel_all, cancel_one, describe


def _ops(log):
    return [Operation(k, k.title(), (lambda k=k: log.append(k))) for k in ("clean", "book", "project")]


def test_cancelling_one_leaves_the_others_running():
    log: list[str] = []
    ops = _ops(log)
    assert cancel_one(ops, "book").label == "Book"
    assert log == ["book"]
    assert cancel_one(ops, "missing") is None and log == ["book"]


def test_cancel_all_and_description():
    log: list[str] = []
    ops = _ops(log)
    cancel_all(ops)
    assert log == ["clean", "book", "project"]
    assert describe(ops) == "Clean · Book · Project"
