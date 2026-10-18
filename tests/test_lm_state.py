from core import lm_state
from core.lm_state import LmStateGuard


def _patch(monkeypatch, snapshots):
    calls = []
    seq = iter(snapshots)
    monkeypatch.setattr(lm_state, "loaded_llms", lambda url, timeout=5: next(seq))
    monkeypatch.setattr(lm_state, "unload", lambda ids: calls.append(list(ids)))
    return calls


def test_unloads_only_models_the_job_loaded(monkeypatch):
    calls = _patch(monkeypatch, [frozenset({"mine"}), frozenset({"mine", "gemma"})])
    guard = LmStateGuard()
    guard.acquire("http://x/v1")
    guard.release()
    assert calls == [["gemma"]]


def test_nested_jobs_restore_once_at_the_end(monkeypatch):
    calls = _patch(monkeypatch, [frozenset(), frozenset({"gemma"})])
    guard = LmStateGuard()
    guard.acquire("u")
    guard.acquire("u")
    guard.release()
    assert calls == []
    guard.release()
    assert calls == [["gemma"]]


def test_unreachable_server_at_start_unloads_nothing(monkeypatch):
    calls = _patch(monkeypatch, [None])
    guard = LmStateGuard()
    guard.acquire("u")
    guard.release()
    assert calls == []


def test_nothing_new_loaded_unloads_nothing(monkeypatch):
    calls = _patch(monkeypatch, [frozenset({"a"}), frozenset({"a"})])
    guard = LmStateGuard()
    guard.acquire("u")
    guard.release()
    assert calls == []
