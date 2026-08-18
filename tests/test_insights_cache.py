"""Unit + integration tests for core/insights_cache.py — the mechanism
that stops "chapters" (or any insight type) from being computed twice
when both YouTubePanel and InsightsPanel want it for the same transcript.
See docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R8.
"""

from __future__ import annotations

import sys
import types

import pytest

from core.insights_cache import InsightsCache


# ---------------------------------------------------------------------------
# InsightsCache itself
# ---------------------------------------------------------------------------

def test_key_is_stable_for_identical_inputs():
    a = InsightsCache.key("chapters", "prompt text", "lmstudio")
    b = InsightsCache.key("chapters", "prompt text", "lmstudio")
    assert a == b


def test_key_differs_by_insight_type():
    a = InsightsCache.key("chapters", "prompt text", "lmstudio")
    b = InsightsCache.key("action_items", "prompt text", "lmstudio")
    assert a != b


def test_key_differs_by_prompt():
    a = InsightsCache.key("chapters", "prompt A", "lmstudio")
    b = InsightsCache.key("chapters", "prompt B", "lmstudio")
    assert a != b


def test_key_differs_by_provider():
    a = InsightsCache.key("chapters", "prompt text", "lmstudio")
    b = InsightsCache.key("chapters", "prompt text", "anthropic")
    assert a != b


def test_get_miss_returns_none():
    cache = InsightsCache()
    assert cache.get("nonexistent") is None


def test_put_then_get_roundtrips():
    cache = InsightsCache()
    key = InsightsCache.key("chapters", "p", "lmstudio")
    cache.put(key, [{"start": 0, "title": "Intro"}])
    assert cache.get(key) == [{"start": 0, "title": "Intro"}]


def test_clear_empties_the_cache():
    cache = InsightsCache()
    key = InsightsCache.key("chapters", "p", "lmstudio")
    cache.put(key, ["x"])
    cache.clear()
    assert cache.get(key) is None


# ---------------------------------------------------------------------------
# InsightsWorker integration — the actual "chapters computed twice" bug
# ---------------------------------------------------------------------------

class _CountingLMStudioClient:
    """Counts real chat_completion_stream calls across all instances, so
    the test can assert the LLM was hit exactly once even though two
    separate InsightsWorker/client instances are involved (matching how
    YouTubePanel and InsightsPanel each construct their own client)."""

    call_count = 0

    def __init__(self, base_url="http://localhost:1234/v1", api_key="", model=""):
        self.base_url = base_url

    def chat_completion_stream(self, **kwargs):
        type(self).call_count += 1
        return '[{"start": 0, "title": "Intro"}]'


@pytest.fixture(autouse=True)
def _stub_lm_client(monkeypatch):
    _CountingLMStudioClient.call_count = 0
    lm_stub = types.ModuleType("core.lm_client")
    lm_stub.LMStudioClient = _CountingLMStudioClient
    lm_stub.DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"
    monkeypatch.setitem(sys.modules, "core.lm_client", lm_stub)


def _segments():
    return [{"start": 0, "text": "hello world", "speaker": None}]


def test_second_worker_reuses_first_workers_result_via_shared_cache():
    """The exact bug: YouTubePanel and InsightsPanel each build their own
    InsightsWorker for "chapters" from the same segments. Sharing one
    InsightsCache between them must mean only one real LLM call happens."""
    from core.insights_worker import InsightsWorker

    cache = InsightsCache()

    first = InsightsWorker("chapters", _segments(), "http://localhost:1234/v1", cache=cache)
    first_result = {}
    first.finished.connect(lambda t, data: first_result.update(type=t, data=data))
    first._execute()

    second = InsightsWorker("chapters", _segments(), "http://localhost:1234/v1", cache=cache)
    second_result = {}
    second.finished.connect(lambda t, data: second_result.update(type=t, data=data))
    second._execute()

    assert _CountingLMStudioClient.call_count == 1, (
        "second worker should have reused the cached result, not called the LLM again"
    )
    assert first_result["data"] == second_result["data"] == [{"start": 0, "title": "Intro"}]


def test_without_a_shared_cache_the_llm_is_called_twice():
    """Regression guard for the fix itself: two workers with no cache (the
    pre-fix default, cache=None) must each make their own call — proves
    the dedup above comes from the cache, not from some other change."""
    from core.insights_worker import InsightsWorker

    InsightsWorker("chapters", _segments(), "http://localhost:1234/v1")._execute()
    InsightsWorker("chapters", _segments(), "http://localhost:1234/v1")._execute()

    assert _CountingLMStudioClient.call_count == 2


def test_different_insight_type_is_not_reused_from_cache():
    from core.insights_worker import InsightsWorker

    cache = InsightsCache()
    InsightsWorker("chapters", _segments(), "http://localhost:1234/v1", cache=cache)._execute()
    InsightsWorker("action_items", _segments(), "http://localhost:1234/v1", cache=cache)._execute()

    assert _CountingLMStudioClient.call_count == 2


def test_different_segments_are_not_reused_from_cache():
    from core.insights_worker import InsightsWorker

    cache = InsightsCache()
    InsightsWorker("chapters", _segments(), "http://localhost:1234/v1", cache=cache)._execute()
    other_segments = [{"start": 0, "text": "completely different transcript", "speaker": None}]
    InsightsWorker("chapters", other_segments, "http://localhost:1234/v1", cache=cache)._execute()

    assert _CountingLMStudioClient.call_count == 2


def test_different_provider_is_not_reused_from_cache():
    """YouTubePanel may use a cloud provider while InsightsPanel always
    uses lmstudio — those must never share a cache entry."""
    import core.ai_provider as ai_provider_module
    from core.insights_worker import InsightsWorker

    class _FakeProviderSettings:
        kind = "anthropic"

    def _fake_create_client(ps):
        return _CountingLMStudioClient()

    import unittest.mock
    with unittest.mock.patch.object(ai_provider_module, "create_client", _fake_create_client):
        cache = InsightsCache()
        InsightsWorker(
            "chapters", _segments(), "http://localhost:1234/v1", cache=cache,
        )._execute()
        InsightsWorker(
            "chapters", _segments(), "http://localhost:1234/v1", cache=cache,
            provider=_FakeProviderSettings(),
        )._execute()

    assert _CountingLMStudioClient.call_count == 2


def test_cancelled_result_is_not_cached():
    """A cancelled run emits an empty list for cleanup purposes — that
    must not poison the cache for a later, real request."""
    from core.insights_worker import InsightsWorker

    cache = InsightsCache()
    worker = InsightsWorker("chapters", _segments(), "http://localhost:1234/v1", cache=cache)
    worker._cancelled.set()
    results = []
    worker.finished.connect(lambda t, data: results.append(data))
    worker._execute()
    assert results == [[]]
    assert _CountingLMStudioClient.call_count == 1  # the cancelled run's own call

    # A fresh worker for the same inputs must still hit the LLM for real —
    # proving the cancelled run's "[]" was not cached as if it were the
    # actual chapters result.
    InsightsWorker("chapters", _segments(), "http://localhost:1234/v1", cache=cache)._execute()
    assert _CountingLMStudioClient.call_count == 2
