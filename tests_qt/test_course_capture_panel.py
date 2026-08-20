"""Real-Qt regressions for the Course Capture panel's queue-driving logic.

Exercises the panel's reaction to LiveRuntime signals directly (finished/
error) rather than a real ScreenCaptureKit capture — that path is covered
by manual verification (see ROADMAP.md, R-Course-Capture.1/.6) since it
needs a real macOS permission grant and playing audio.
"""

from __future__ import annotations

from domain.course_capture import CaptureItemStatus
from domain.transcription import Segment, TranscriptionResult


def _make_panel():
    from ui.course_capture_panel import CourseCapturePanel

    return CourseCapturePanel()


def test_add_lesson_updates_list_and_enables_start(process_events):
    panel = _make_panel()
    assert panel.start_stop_btn.isEnabled() is False

    panel.queue.add_item("Урок 1")
    panel._refresh_list()
    process_events()

    assert panel.item_list.count() == 1
    assert panel.start_stop_btn.isEnabled() is True
    assert panel.combine_btn.isEnabled() is False  # nothing DONE yet
    panel.shutdown()


def test_runtime_finished_saves_history_and_marks_done(monkeypatch, process_events):
    panel = _make_panel()
    item = panel.queue.add_item("Урок 1")
    panel.queue.start_recording(item.id)
    panel._active_item_id = item.id
    panel._refresh_list()

    saved = {}

    class _FakeStore:
        def add(self, result, source_path, model="", source_kind="file", source_name=None):
            saved["result"] = result
            saved["source_name"] = source_name
            return 42

    monkeypatch.setattr("core.history.get_history_store", lambda: _FakeStore())

    received_ids = []
    panel.lesson_saved.connect(received_ids.append)

    result = TranscriptionResult(
        segments=[Segment(start=0.0, end=1.0, text="hello")], language="ru", duration=1.0,
    )
    panel._on_runtime_finished(result, "")
    process_events()

    assert panel._active_item_id is None
    assert item.status is CaptureItemStatus.DONE
    assert saved["source_name"] == "Урок 1"
    assert received_ids == [42]
    assert panel.combine_btn.isEnabled() is True
    assert panel.export_per_lesson_btn.isEnabled() is True
    panel.shutdown()


def test_runtime_error_marks_item_failed_and_reenables_start(process_events):
    panel = _make_panel()
    item = panel.queue.add_item("Урок 1")
    panel.queue.start_recording(item.id)
    panel._active_item_id = item.id
    panel._refresh_list()

    # A fresh LiveRuntime was never started, so is_running() is False —
    # matches the real flow, where the runtime has already torn itself
    # down by the time error_occurred reaches this handler.
    panel._on_runtime_error("system", "helper crashed")
    process_events()

    assert panel._active_item_id is None
    assert item.status is CaptureItemStatus.ERROR
    assert item.error == "helper crashed"
    panel.shutdown()


def test_runtime_error_from_unrelated_source_is_ignored(process_events):
    panel = _make_panel()
    item = panel.queue.add_item("Урок 1")
    panel.queue.start_recording(item.id)
    panel._active_item_id = item.id

    panel._on_runtime_error("mic", "mic device busy")
    process_events()

    assert panel._active_item_id == item.id
    assert item.status is CaptureItemStatus.RECORDING
    panel.shutdown()


def test_remove_item_ignored_while_it_is_recording(process_events):
    panel = _make_panel()
    item = panel.queue.add_item("Урок 1")
    panel.queue.start_recording(item.id)
    panel._active_item_id = item.id
    panel._refresh_list()

    panel._remove_item(item.id)

    assert panel.queue.item_by_id(item.id) is item
    panel.shutdown()


def _finish_one_lesson(panel, title="Урок 1", text="hello"):
    item = panel.queue.add_item(title)
    panel.queue.start_recording(item.id)
    panel.queue.finish_recording(item.id, TranscriptionResult(
        segments=[Segment(start=0.0, end=1.0, text=text)], language="ru", duration=1.0,
    ))
    panel._refresh_list()
    return item


def test_combine_into_document_writes_chosen_format(tmp_path, monkeypatch, process_events):
    panel = _make_panel()
    _finish_one_lesson(panel)

    target = tmp_path / "combined.md"
    monkeypatch.setattr(
        "ui.course_capture_panel.QFileDialog.getSaveFileName",
        lambda *a, **k: (str(target), "Markdown (*.md)"),
    )
    index = panel.format_combo.findData("md")
    panel.format_combo.setCurrentIndex(index)

    panel._combine_into_document()
    process_events()

    assert target.read_text(encoding="utf-8") == "## Урок 1\n\nhello"
    panel.shutdown()


def test_combine_into_document_cancelled_dialog_writes_nothing(tmp_path, monkeypatch, process_events):
    panel = _make_panel()
    _finish_one_lesson(panel)
    monkeypatch.setattr(
        "ui.course_capture_panel.QFileDialog.getSaveFileName", lambda *a, **k: ("", "")
    )
    panel._combine_into_document()
    process_events()
    assert list(tmp_path.iterdir()) == []
    panel.shutdown()


def test_export_per_lesson_writes_one_file_per_done_item(tmp_path, monkeypatch, process_events):
    panel = _make_panel()
    _finish_one_lesson(panel, "Урок 1", "hello")
    _finish_one_lesson(panel, "Урок 2", "world")

    monkeypatch.setattr(
        "ui.course_capture_panel.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(tmp_path),
    )
    index = panel.format_combo.findData("txt")
    panel.format_combo.setCurrentIndex(index)

    panel._export_per_lesson()
    process_events()

    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["01 - Урок 1.txt", "02 - Урок 2.txt"]
    assert (tmp_path / "01 - Урок 1.txt").read_text(encoding="utf-8") == "## Урок 1\n\nhello"
    panel.shutdown()


def test_course_name_edit_persists_to_queue_and_config(process_events):
    panel = _make_panel()
    panel.course_name_edit.setText("Вокальный курс")
    panel._on_course_title_changed()

    from config import get_config

    assert panel.queue.course_title == "Вокальный курс"
    assert get_config().course_capture_course_name == "Вокальный курс"
    panel.shutdown()
