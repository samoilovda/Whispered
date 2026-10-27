"""The persistent queue's storage and run helpers (UX-14 / T14)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from application import queue_run, queue_store
from application.job_engine import JobRun
from config import Config
from core.history import HistoryStore
from domain import queue as q
from domain.job import JobSpec, StepOutcome, StepSpec, StepStatus
from domain.recipe import Recipe


@pytest.fixture
def store(monkeypatch, tmp_path):
    db = HistoryStore(db_path=tmp_path / "h.db")
    monkeypatch.setattr("core.history.get_history_store", lambda: db)
    return db


def _snapshot(model="turbo", steps=("transcribe", "article")):
    recipe = Recipe(name="r", steps=steps)
    return queue_store.make_snapshot(recipe, {"model_name": model, "language": "ru"})


def test_enqueue_keeps_order_and_skips_a_file_already_waiting(store):
    added = queue_store.enqueue(["/a.mp3", "/b.mp3"], _snapshot())
    assert list(added) == ["/a.mp3", "/b.mp3"]
    assert queue_store.enqueue(["/a.mp3", "/c.mp3"], _snapshot()) .keys() == {"/c.mp3"}
    assert [i.source_path for i in queue_store.load_items()] == ["/a.mp3", "/b.mp3", "/c.mp3"]


def test_snapshot_round_trips_and_is_independent_of_later_settings(store):
    queue_store.enqueue(["/a.mp3"], _snapshot(model="tiny"))
    queue_store.enqueue(["/b.mp3"], _snapshot(model="large"))
    a, b = queue_store.load_items()
    assert a.snapshot.transcription["model_name"] == "tiny"
    assert b.snapshot.transcription["model_name"] == "large"
    assert a.snapshot.follow_up_steps == ("article",)


def test_finished_file_can_be_queued_again_but_a_waiting_one_cannot(store):
    ids = queue_store.enqueue(["/a.mp3"], _snapshot())
    queue_store.set_state(ids["/a.mp3"], q.DONE)
    assert queue_store.enqueue(["/a.mp3"], _snapshot())  # done → a new item is fine


def test_crash_leaves_running_items_interrupted_and_retry_puts_them_back(store):
    ids = queue_store.enqueue(["/a.mp3", "/b.mp3"], _snapshot())
    queue_store.set_state(ids["/a.mp3"], q.RUNNING, stage="transcribe")
    assert queue_store.mark_interrupted() == 1
    states = {i.source_path: i.state for i in queue_store.load_items()}
    assert states == {"/a.mp3": q.INTERRUPTED, "/b.mp3": q.PENDING}
    assert queue_store.retry(ids["/a.mp3"]) and not queue_store.retry(ids["/b.mp3"])


def test_reorder_remove_and_clear_finished(store):
    ids = queue_store.enqueue(["/a", "/b", "/c"], _snapshot())
    queue_store.reorder([ids["/c"], ids["/a"], ids["/b"]])
    assert [i.source_path for i in queue_store.load_items()] == ["/c", "/a", "/b"]
    queue_store.remove(ids["/a"])
    queue_store.set_state(ids["/b"], q.FAILED, error="x")
    assert queue_store.clear_finished() == 1
    assert [i.source_path for i in queue_store.load_items()] == ["/c"]


def test_deleting_a_record_keeps_its_queue_item_without_the_link(store):
    result = SimpleNamespace(segments=[], language="en", duration=1.0)
    rid = store.add(result, source_path="/a.mp3")
    item_id = queue_store.enqueue(["/a.mp3"], _snapshot())["/a.mp3"]
    queue_store.set_state(item_id, q.DONE, record_id=rid)
    store.delete(rid)
    assert queue_store.load_item(item_id).record_id is None


def test_unknown_state_and_field_are_rejected(store):
    item_id = queue_store.enqueue(["/a"], _snapshot())["/a"]
    with pytest.raises(ValueError):
        queue_store.set_state(item_id, "weird")
    with pytest.raises(ValueError):
        store.queue_update(item_id, bogus=1)


def test_finish_run_maps_a_run_to_a_queue_state():
    spec = JobSpec(name="r", steps=(StepSpec("transcribe"), StepSpec("article", depends_on=("transcribe",))))

    def run_with(status, error=""):
        run = JobRun(spec=spec)
        run.outcomes["transcribe"] = StepOutcome("transcribe", StepStatus.SUCCEEDED)
        run.outcomes["article"] = StepOutcome("article", status, error=error)
        return run

    assert queue_run.finish_run(run_with(StepStatus.SUCCEEDED))[0] == "done"
    assert queue_run.finish_run(run_with(StepStatus.CANCELLED))[0] == "cancelled"
    state, error, _ = queue_run.finish_run(run_with(StepStatus.FAILED, "LM Studio is not responding"))
    assert state == "failed" and error == "article:provider_unavailable"


def test_queued_cover_step_gets_a_renderer_and_each_item_its_own_cover(store, tmp_path, monkeypatch):
    from application.steps import _cover_runner
    from domain.transcription import Segment, TranscriptionResult

    class FakeImage:
        def save(self, path, fmt):
            with open(path, "wb") as handle:
                handle.write(b"PNG")
            return True

    seen = []

    def renderer(template, layout, variant, slots, size, decor_set):
        seen.append((layout, slots.get("title")))
        return FakeImage(), []

    monkeypatch.setattr("core.paths.artifact_dir", lambda record_id, source: tmp_path / f"a-{record_id}")
    recipe = Recipe(name="Video", steps=("transcribe", "cover"))
    result = TranscriptionResult([Segment(0.0, 1.0, "hello")], "en", 1.0)
    covers = []
    for record_id, name in ((1, "first.mp3"), (2, "second.mp3")):
        item = SimpleNamespace(
            source_path=str(tmp_path / name), run_id=None,
            snapshot=q.QueueSnapshot(recipe.to_dict(), {}, {}))
        (tmp_path / f"a-{record_id}").mkdir()
        follow_up = queue_run.prepare_follow_up(
            item, result, record_id, cfg=Config(),
            extra_params={"cover_renderer": renderer, "cover_layout": "solo"})
        assert follow_up.context.params["cover_renderer"] is renderer
        _cover_runner(follow_up.context)()
        covers.append(tmp_path / f"a-{record_id}" / "cover.png")
    assert all(path.is_file() for path in covers) and len(seen) == 2
