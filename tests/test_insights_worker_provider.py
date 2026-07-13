"""Unit tests for InsightsWorker's yt_questions type and provider selection."""

import sys
import types

import pytest

# Qt stand-ins come from tests/conftest.py.


class _StubLMStudioClient:
    def __init__(self, base_url="http://localhost:1234/v1", api_key="", model=""):
        self.base_url = base_url
        self._api_key = api_key
        self._model = model

    def chat_completion_stream(self, **kwargs):
        return '[{"start": 0, "title": "Q1"}]'


from core.insights_worker import InsightsWorker, _INSIGHT_TYPES


@pytest.fixture(autouse=True)
def _stub_lm_client(monkeypatch):
    # core.insights_worker._execute() imports LMStudioClient *lazily*
    # (inside the function body) so it re-resolves sys.modules['core.lm_client']
    # on every call, not just once at collection time. Install the stub via
    # monkeypatch (auto-reverted after this test) rather than a permanent
    # module-level assignment — a permanent one would leak into whichever
    # test runs next and could hit the real network (see test_ai_provider.py
    # for the incident this caused).
    lm_stub = types.ModuleType("core.lm_client")
    lm_stub.LMStudioClient = _StubLMStudioClient
    lm_stub.DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"
    monkeypatch.setitem(sys.modules, "core.lm_client", lm_stub)


def test_yt_questions_is_a_known_insight_type():
    assert "yt_questions" in _INSIGHT_TYPES


def test_worker_accepts_yt_questions_type():
    worker = InsightsWorker("yt_questions", [], "http://localhost:1234/v1")
    assert worker._type == "yt_questions"


def test_default_provider_is_none():
    worker = InsightsWorker("yt_questions", [], "http://localhost:1234/v1")
    assert worker._provider is None


def test_execute_without_provider_uses_lmstudio_client(monkeypatch):
    segments = [{"start": 0, "text": "hello", "speaker": None}]
    worker = InsightsWorker("yt_questions", segments, "http://localhost:1234/v1")
    worker._execute()
    finished_calls = worker.finished.calls
    assert len(finished_calls) == 0  # nothing connected; just verifying no exception


def test_execute_with_provider_uses_create_client(monkeypatch):
    import core.ai_provider as ai_provider_module

    created = {}

    class _FakeProviderSettings:
        kind = "anthropic"

    def _fake_create_client(ps):
        created["called"] = True
        return _StubLMStudioClient()

    monkeypatch.setattr(ai_provider_module, "create_client", _fake_create_client)

    segments = [{"start": 0, "text": "hello", "speaker": None}]
    worker = InsightsWorker(
        "yt_questions", segments, "http://localhost:1234/v1",
        provider=_FakeProviderSettings(),
    )
    result = {}
    worker.finished.connect(lambda t, data: result.update(type=t, data=data))
    worker._execute()
    assert created["called"] is True
    assert result["type"] == "yt_questions"
    assert result["data"] == [{"start": 0, "title": "Q1"}]
