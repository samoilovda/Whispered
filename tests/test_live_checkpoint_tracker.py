"""Unit tests for ui/live_checkpoint_tracker.py — no Qt required.

HistoryStore (core/history.py) is plain SQLite, so these exercise real
checkpoint I/O against a temp database rather than a mock.
"""

from types import SimpleNamespace

import pytest

from core.history import HistoryStore
from ui.live_checkpoint_tracker import LiveCheckpointTracker


def _segment(start, end, text="hello", speaker=None):
    return SimpleNamespace(start=start, end=end, text=text, speaker=speaker)


@pytest.fixture
def store(tmp_path):
    return HistoryStore(db_path=tmp_path / "live_checkpoint.db")


class TestStartResetsState:
    def test_clears_a_previous_session(self):
        t = LiveCheckpointTracker()
        t.start("Live 2026-01-01 10:00", "base")
        t.accept_final("a", _segment(0.0, 1.0))
        t.history_record_id = 42

        t.start("Live 2026-01-01 11:00", "large-v3")

        assert t.history_record_id is None
        assert t.source_name == "Live 2026-01-01 11:00"
        assert t.model_name == "large-v3"
        assert t.build_result("en").segments == []


class TestBuildResult:
    def test_empty_session_has_no_segments_and_zero_duration(self):
        t = LiveCheckpointTracker()
        t.start("Live", "base")
        result = t.build_result("en")
        assert result.segments == []
        assert result.duration == 0.0
        assert result.language == "en"

    def test_segments_sorted_by_start_then_end(self):
        t = LiveCheckpointTracker()
        t.start("Live", "base")
        t.accept_final("b", _segment(5.0, 8.0, text="second"))
        t.accept_final("a", _segment(0.0, 3.0, text="first"))

        result = t.build_result("en")

        assert [s.text for s in result.segments] == ["first", "second"]

    def test_duration_is_the_latest_segment_end(self):
        t = LiveCheckpointTracker()
        t.start("Live", "base")
        t.accept_final("a", _segment(0.0, 3.0))
        t.accept_final("b", _segment(5.0, 12.5))

        assert t.build_result("en").duration == 12.5

    def test_re_finalizing_the_same_segment_id_replaces_it(self):
        """A later revision of the same segment (still keyed by
        segment_id) must overwrite, not duplicate."""
        t = LiveCheckpointTracker()
        t.start("Live", "base")
        t.accept_final("a", _segment(0.0, 1.0, text="draft"))
        t.accept_final("a", _segment(0.0, 1.0, text="final"))

        result = t.build_result("en")

        assert len(result.segments) == 1
        assert result.segments[0].text == "final"

    def test_speaker_names_are_always_mic_and_system(self):
        t = LiveCheckpointTracker()
        t.start("Live", "base")
        result = t.build_result("en")
        assert set(result.speaker_names) == {"mic", "system"}


class TestCheckpoint:
    def test_no_segments_does_not_touch_the_store(self, store):
        t = LiveCheckpointTracker()
        t.start("Live", "base")

        wrote = t.checkpoint(store, "en")

        assert wrote is False
        assert t.history_record_id is None
        assert store.count() == 0

    def test_first_checkpoint_creates_a_row(self, store):
        t = LiveCheckpointTracker()
        t.start("Live 2026-01-01 10:00", "base")
        t.accept_final("a", _segment(0.0, 1.0, text="hello"))

        wrote = t.checkpoint(store, "en")

        assert wrote is True
        assert t.history_record_id is not None
        record = store.get_record(t.history_record_id)
        assert record["source_kind"] == "live"
        assert record["source_name"] == "Live 2026-01-01 10:00"
        assert record["model"] == "base"
        assert record["payload"]["segments"][0]["text"] == "hello"

    def test_second_checkpoint_updates_the_same_row(self, store):
        t = LiveCheckpointTracker()
        t.start("Live", "base")
        t.accept_final("a", _segment(0.0, 1.0, text="first"))
        t.checkpoint(store, "en")
        first_id = t.history_record_id

        t.accept_final("b", _segment(1.0, 2.0, text="second"))
        t.checkpoint(store, "en")

        assert t.history_record_id == first_id
        assert store.count() == 1
        record = store.get_record(first_id)
        texts = [s["text"] for s in record["payload"]["segments"]]
        assert texts == ["first", "second"]
