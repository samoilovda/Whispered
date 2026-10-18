"""The persistent recipe queue end to end (UI/UX audit 2026-10-18,
UX-14 / T14). Transcription itself is simulated by emitting the batch
processor's own signals; the recipe steps run through the real JobRunner
with a fake "insights" runner (no LM Studio)."""

from __future__ import annotations

import dataclasses

import pytest

import config
from application import queue_store
from core.history import HistoryStore
from core.i18n import load_locale
from domain import queue as q
from domain.recipe import Recipe
from transcriber import Segment, TranscriptionResult

FAIL_ON: set = set()
RUNS: list = []


def _result(text="hello"):
    return TranscriptionResult(segments=[Segment(0.0, 2.0, text)], language="en", duration=2.0)


@pytest.fixture
def env(monkeypatch, tmp_path, process_events):
    from application import steps
    from ui.main_window import MainWindow

    load_locale("en")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    store = HistoryStore(db_path=tmp_path / "history.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    monkeypatch.setattr("application.run_store._db_path", lambda: tmp_path / "history.sqlite3")
    FAIL_ON.clear()
    RUNS.clear()

    real = steps.STEP_REGISTRY["insights"]

    def make_runner(context):
        def run():
            RUNS.append(context.source_path)
            if context.source_path in FAIL_ON:
                raise RuntimeError("LM Studio is not responding")
            return {"summary": "ok"}
        return run

    fake = dataclasses.replace(real, make_runner=make_runner, make_artifact=lambda c: None)
    monkeypatch.setitem(steps.STEP_REGISTRY, "insights", fake)

    files = []
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        path = tmp_path / name
        path.write_bytes(b"\0" * 64)
        files.append(str(path))
    windows = []

    def make(model="turbo"):
        window = MainWindow()
        window.show()
        process_events()
        recipe = Recipe(name="Notes", steps=("transcribe", "insights"))
        window.batch_panel.snapshot_provider = lambda m=model: queue_store.make_snapshot(
            recipe, {"model_name": m, "language": "auto", "translate": False,
                     "n_threads": 4, "enable_diarization": False, "use_gpu": True})
        windows.append(window)
        return window

    yield make, store, files, process_events
    for window in windows:
        window.queue_controller.shutdown()
        window.close()
    process_events()


def _wait_idle(window, process_events):
    controller = window.queue_controller
    for _ in range(200):
        process_events()
        if controller._active is not None:
            controller._active[2].wait(5000)
        process_events()
        if not controller.is_busy():
            return
    raise AssertionError("queue did not go idle")


def _transcribe(window, index, process_events, text="hello"):
    proc = window.batch_panel.processor
    item = proc.items[index]
    item.status = __import__("batch_processor").BatchStatus.PROCESSING
    proc.item_started.emit(index)
    item.status = __import__("batch_processor").BatchStatus.COMPLETE
    proc.item_finished.emit(index, _result(text))
    _wait_idle(window, process_events)


def _states():
    return {i.source_path.rsplit("/", 1)[-1]: i.state for i in queue_store.load_items()}


def test_three_files_second_fails_first_is_not_redone_and_retry_is_individual(env):
    make, store, files, process_events = env
    window = make()
    window.batch_panel.add_files(files)
    assert [i.state for i in queue_store.load_items()] == [q.PENDING] * 3

    FAIL_ON.add(files[1])
    for index in range(3):
        _transcribe(window, index, process_events, text=f"file {index}")
    assert _states() == {"a.mp3": q.DONE, "b.mp3": q.FAILED, "c.mp3": q.DONE}
    assert store.count() == 3                       # one record per file
    failed = window.batch_panel.processor.items[1]
    assert "Notes" not in failed.follow_up_error and failed.follow_up_error
    runs_before = list(RUNS)

    FAIL_ON.clear()
    window.queue_controller.retry(1)                # only the failed one
    _wait_idle(window, process_events)
    assert _states()["b.mp3"] == q.DONE
    assert RUNS == runs_before + [files[1]]         # a and c were not run again
    assert store.count() == 3                       # and nothing was transcribed twice
    assert not window.batch_panel.processor.items[1].follow_up_error


def test_queued_settings_are_frozen_at_queue_time(env):
    make, store, files, process_events = env
    window = make(model="tiny")
    window.batch_panel.add_files(files[:1])
    window.model_combo.setCurrentIndex(window.model_combo.count() - 1)  # change the controls later
    item = window.batch_panel.processor.items[0]
    assert item.transcription["model_name"] == "tiny"
    assert queue_store.load_items()[0].snapshot.transcription["model_name"] == "tiny"


def test_restart_shows_unfinished_items_as_interrupted_and_starts_nothing(env):
    make, store, files, process_events = env
    window = make()
    window.batch_panel.add_files(files)
    proc = window.batch_panel.processor
    proc.item_started.emit(0)                       # a.mp3 was running when the app died
    assert _states()["a.mp3"] == q.RUNNING
    window.queue_controller.shutdown()
    window.close()

    fresh = make()
    states = _states()
    assert states["a.mp3"] == q.INTERRUPTED and states["b.mp3"] == q.PENDING
    assert [i.filepath for i in fresh.batch_panel.processor.items] == files
    assert not fresh.batch_panel.processor.is_processing and not fresh.queue_controller.is_busy()
    assert RUNS == []


def test_an_interrupted_item_with_a_record_resumes_only_its_steps(env):
    make, store, files, process_events = env
    window = make()
    window.batch_panel.add_files(files[:1])
    FAIL_ON.add(files[0])
    _transcribe(window, 0, process_events)
    assert _states()["a.mp3"] == q.FAILED and store.count() == 1
    item_id = queue_store.load_items()[0].id
    queue_store.set_state(item_id, q.INTERRUPTED)    # as if the app had died mid-steps
    window.queue_controller.shutdown()
    window.close()

    FAIL_ON.clear()
    fresh = make()
    item = fresh.batch_panel.processor.items[0]
    assert item.awaiting_follow_up                    # waiting for Start, transcript saved
    assert RUNS == [files[0]]                         # (only the earlier failed attempt)
    fresh.queue_controller.start_backlog()
    _wait_idle(fresh, process_events)
    assert _states()["a.mp3"] == q.DONE and store.count() == 1  # no second transcription
    assert RUNS == [files[0], files[0]]


def test_deleted_source_without_a_record_is_failed_not_restored(env):
    make, store, files, process_events = env
    window = make()
    window.batch_panel.add_files(files[:2])
    window.queue_controller.shutdown()
    window.close()
    import os
    os.remove(files[0])
    fresh = make()
    assert _states()["a.mp3"] == q.FAILED
    assert [i.filepath for i in fresh.batch_panel.processor.items] == [files[1]]


def test_duplicate_watch_folder_events_queue_once_and_start_nothing(env):
    make, store, files, process_events = env
    window = make()
    window._on_watch_folder_file_found(files[0])
    window._on_watch_folder_file_found(files[0])
    assert len(queue_store.load_items()) == 1
    assert not window.batch_panel.processor.is_processing and not window.queue_controller.is_busy()
    assert RUNS == []


def test_cancel_marks_waiting_items_cancelled_and_they_stay_retryable(env):
    make, store, files, process_events = env
    window = make()
    window.batch_panel.add_files(files[:2])
    proc = window.batch_panel.processor
    proc.item_started.emit(0)
    proc.items[0].status = __import__("batch_processor").BatchStatus.COMPLETE
    # Two results arrive while the first run's steps are still waiting.
    window.queue_controller._waiting.append((proc.items[0], _result()))
    window.queue_controller.cancel()
    assert _states()["a.mp3"] == q.CANCELLED
    assert queue_store.retry(queue_store.load_items()[0].id)


def test_reorder_and_remove_are_stored(env):
    make, store, files, process_events = env
    window = make()
    window.batch_panel.add_files(files)
    ids = [i.queue_id for i in window.batch_panel.processor.items]
    window.batch_panel.order_changed.emit([ids[2], ids[0], ids[1]])
    assert [i.source_path for i in queue_store.load_items()] == [files[2], files[0], files[1]]
    window.batch_panel._remove_item(0)
    assert len(queue_store.load_items()) == 2
