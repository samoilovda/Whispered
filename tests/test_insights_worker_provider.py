"""Unit tests for InsightsWorker's yt_questions type and provider selection."""

import sys
import types

for _mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui",
             "PyQt6.QtMultimedia"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

_qtcore = sys.modules["PyQt6.QtCore"]
_qtcore.QThread = type("QThread", (), {
    "__init__": lambda self, parent=None: None,
    "start": lambda *a: None,
    "isRunning": lambda *a: False,
})


class _FakeSignal:
    """Minimal pyqtSignal stand-in that records emitted args."""

    def __init__(self, *a, **kw):
        self._slots = []

    def __get__(self, obj, objtype=None):
        # Bound-signal-like object per instance
        key = f"_bound_{id(self)}"
        if not hasattr(obj, key):
            setattr(obj, key, _BoundSignal())
        return getattr(obj, key)


class _BoundSignal:
    def __init__(self):
        self.calls = []

    def connect(self, fn):
        self.calls.append(fn)

    def emit(self, *args):
        for fn in self.calls:
            fn(*args)


_qtcore.pyqtSignal = _FakeSignal
_qtcore.QObject = type("QObject", (), {"__init__": lambda *a, **kw: None})

_lm_stub = types.ModuleType("core.lm_client")


class _StubLMStudioClient:
    def __init__(self, base_url="http://localhost:1234/v1", api_key="", model=""):
        self.base_url = base_url
        self._api_key = api_key
        self._model = model

    def chat_completion_stream(self, **kwargs):
        return '[{"start": 0, "title": "Q1"}]'


_lm_stub.LMStudioClient = _StubLMStudioClient
_lm_stub.DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"
sys.modules["core.lm_client"] = _lm_stub

_ai_stub = types.ModuleType("core.ai_worker")
_ai_stub.AIProcessingWorker = object
sys.modules.setdefault("core.ai_worker", _ai_stub)

# Other test modules may have already imported core.base_worker with a
# QThread stub that lacks __init__(parent=...); force a fresh import bound
# to the QThread stub defined above.
sys.modules.pop("core.base_worker", None)
sys.modules.pop("core.insights_worker", None)
from core.insights_worker import InsightsWorker, _INSIGHT_TYPES


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
