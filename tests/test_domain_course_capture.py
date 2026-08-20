"""domain/course_capture.py must stay Qt-free, same rule as
domain/transcription.py — see tests/test_domain_transcription.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from domain.course_capture import CaptureItemStatus, CaptureQueue
from domain.transcription import TranscriptionResult


def test_domain_module_does_not_import_qt_or_ui_or_live():
    src = Path("domain/course_capture.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_prefixes = ("PyQt6", "ui.", "ui", "core.live")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(forbidden_prefixes)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith(forbidden_prefixes):
                pytest.fail(f"domain/course_capture.py imports {node.module!r}")


def test_add_item_and_next_pending_order():
    queue = CaptureQueue()
    first = queue.add_item("Урок 1")
    second = queue.add_item("Урок 2")
    assert queue.next_pending() is first
    queue.start_recording(first.id)
    assert queue.next_pending() is second


def test_only_one_item_can_record_at_a_time():
    queue = CaptureQueue()
    first = queue.add_item("Урок 1")
    second = queue.add_item("Урок 2")
    queue.start_recording(first.id)
    with pytest.raises(ValueError):
        queue.start_recording(second.id)


def test_finish_recording_stores_result_and_marks_done():
    queue = CaptureQueue()
    item = queue.add_item("Урок 1")
    queue.start_recording(item.id)
    result = TranscriptionResult(segments=[], language="ru", duration=12.0)
    queue.finish_recording(item.id, result)
    assert item.status is CaptureItemStatus.DONE
    assert item.result is result
    assert item.is_complete


def test_fail_recording_marks_error_and_clears_on_retry():
    queue = CaptureQueue()
    item = queue.add_item("Урок 1")
    queue.start_recording(item.id)
    queue.fail_recording(item.id, "helper crashed")
    assert item.status is CaptureItemStatus.ERROR
    assert item.error == "helper crashed"
    assert item.is_complete
    # retrying clears the previous error
    queue.start_recording(item.id)
    assert item.error == ""
    assert item.status is CaptureItemStatus.RECORDING


def test_remove_and_move_item():
    queue = CaptureQueue()
    a = queue.add_item("A")
    queue.add_item("B")
    c = queue.add_item("C")
    queue.move_item(c.id, 0)
    assert [item.title for item in queue.items] == ["C", "A", "B"]
    queue.remove_item(a.id)
    assert [item.title for item in queue.items] == ["C", "B"]


def test_item_by_id_unknown_raises_keyerror():
    queue = CaptureQueue()
    with pytest.raises(KeyError):
        queue.item_by_id("missing")
