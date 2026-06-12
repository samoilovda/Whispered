"""Tests for core/history.py — no Qt or network required."""
import sys
import types

# Stub Qt and heavy PyQt6 modules
for _mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui",
             "PyQt6.QtMultimedia"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

# Stub core sub-modules that pull in Qt BEFORE the real package loads
_lm_stub = types.ModuleType("core.lm_client")
_lm_stub.LMStudioClient = object
_lm_stub.DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"
sys.modules.setdefault("core.lm_client", _lm_stub)

_ai_stub = types.ModuleType("core.ai_worker")
_ai_stub.AIProcessingWorker = object
sys.modules.setdefault("core.ai_worker", _ai_stub)

import pytest
from types import SimpleNamespace

from core.history import HistoryStore


def _make_result(segments=None, language="en", duration=60.0):
    if segments is None:
        segments = [
            SimpleNamespace(start=0.0, end=5.0, text="Hello world", speaker="Speaker 1"),
            SimpleNamespace(start=5.0, end=10.0, text="How are you", speaker="Speaker 2"),
        ]
    return SimpleNamespace(segments=segments, language=language, duration=duration)


@pytest.fixture
def store(tmp_path):
    return HistoryStore(db_path=tmp_path / "test_history.db")


class TestHistoryStoreBasics:
    def test_empty_list(self, store):
        assert store.list() == []

    def test_add_and_list(self, store):
        result = _make_result()
        rid = store.add(result, "/tmp/audio.wav", model="large-v3")
        assert isinstance(rid, int)
        records = store.list()
        assert len(records) == 1
        assert records[0].id == rid
        assert records[0].source_name == "audio.wav"
        assert records[0].language == "en"
        assert abs(records[0].duration - 60.0) < 1e-6
        assert records[0].model == "large-v3"

    def test_get_payload(self, store):
        result = _make_result()
        rid = store.add(result, "/tmp/audio.wav")
        payload = store.get(rid)
        assert payload is not None
        assert payload["language"] == "en"
        assert len(payload["segments"]) == 2
        assert payload["segments"][0]["text"] == "Hello world"

    def test_get_nonexistent(self, store):
        assert store.get(9999) is None

    def test_delete(self, store):
        result = _make_result()
        rid = store.add(result, "/tmp/audio.wav")
        assert store.count() == 1
        deleted = store.delete(rid)
        assert deleted is True
        assert store.count() == 0

    def test_delete_nonexistent(self, store):
        assert store.delete(9999) is False

    def test_clear(self, store):
        result = _make_result()
        store.add(result, "/tmp/a.wav")
        store.add(result, "/tmp/b.wav")
        n = store.clear()
        assert n == 2
        assert store.count() == 0


class TestHistorySearch:
    def test_search_finds_text(self, store):
        result = _make_result()
        store.add(result, "/tmp/audio.wav")
        records = store.search("Hello world")
        assert len(records) == 1

    def test_search_no_match(self, store):
        result = _make_result()
        store.add(result, "/tmp/audio.wav")
        records = store.search("xyzzy_not_found")
        assert len(records) == 0


class TestSpeakerNames:
    def test_speaker_names_round_trip(self, store):
        result = _make_result()
        names = {"Speaker 1": "Alice", "Speaker 2": "Bob"}
        rid = store.add(result, "/tmp/audio.wav", speaker_names=names)
        payload = store.get(rid)
        assert payload["speaker_names"] == names

    def test_speaker_names_default_empty(self, store):
        result = _make_result()
        rid = store.add(result, "/tmp/audio.wav")
        payload = store.get(rid)
        assert payload["speaker_names"] == {}


class TestMultipleRecords:
    def test_newest_first(self, store):
        r1 = _make_result(language="en")
        r2 = _make_result(language="ru")
        store.add(r1, "/tmp/a.wav")
        store.add(r2, "/tmp/b.wav")
        records = store.list()
        assert len(records) == 2
        # newest (b.wav) should come first
        assert records[0].source_name == "b.wav"

    def test_limit_offset(self, store):
        r = _make_result()
        for i in range(5):
            store.add(r, f"/tmp/f{i}.wav")
        page1 = store.list(limit=2, offset=0)
        page2 = store.list(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        ids1 = {x.id for x in page1}
        ids2 = {x.id for x in page2}
        assert ids1.isdisjoint(ids2)
