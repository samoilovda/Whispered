"""Unit tests for core/insights_export.py — no Qt required."""

from __future__ import annotations

from core.insights_export import (
    format_action_items_text,
    format_chapters_text,
    format_insight_text,
    format_key_moments_text,
)


class TestFormatChaptersText:
    def test_formats_start_and_title(self):
        chapters = [{"start": 65, "title": "Intro"}, {"start": 130, "title": "Main topic"}]
        result = format_chapters_text(chapters)
        assert "Intro" in result
        assert "Main topic" in result
        assert result.count("\n") == 1  # two lines, one separator

    def test_skips_blank_title(self):
        chapters = [{"start": 0, "title": ""}, {"start": 10, "title": "Real"}]
        result = format_chapters_text(chapters)
        assert "Real" in result
        assert result.count("\n") == 0  # only one line survived

    def test_skips_non_dict_items(self):
        assert format_chapters_text(["not a dict", 42]) == ""

    def test_skips_unparseable_start(self):
        chapters = [{"start": "not-a-number", "title": "X"}]
        assert format_chapters_text(chapters) == ""

    def test_empty_list_returns_empty_string(self):
        assert format_chapters_text([]) == ""


class TestFormatActionItemsText:
    def test_includes_owner_and_deadline_when_present(self):
        items = [{"task": "Ship it", "owner": "Alice", "deadline": "Friday"}]
        result = format_action_items_text(items)
        assert "Ship it" in result
        assert "Alice" in result
        assert "Friday" in result

    def test_omits_owner_and_deadline_when_absent(self):
        items = [{"task": "Just a task"}]
        result = format_action_items_text(items)
        assert result == "• Just a task"

    def test_skips_blank_task(self):
        items = [{"task": ""}, {"task": "Real task"}]
        result = format_action_items_text(items)
        assert result == "• Real task"


class TestFormatKeyMomentsText:
    def test_includes_quote_and_note(self):
        moments = [{"start": 42, "quote": "A memorable line", "note": "Context here"}]
        result = format_key_moments_text(moments)
        assert "A memorable line" in result
        assert "Context here" in result

    def test_omits_note_line_when_absent(self):
        moments = [{"start": 42, "quote": "Just a quote"}]
        result = format_key_moments_text(moments)
        assert "Just a quote" in result
        assert "\n" not in result  # no note -> single line for this entry

    def test_skips_blank_quote(self):
        moments = [{"start": 0, "quote": ""}, {"start": 5, "quote": "Kept"}]
        result = format_key_moments_text(moments)
        assert result.count('"') == 2  # only the kept quote's pair of quotes


class TestFormatInsightText:
    def test_dispatches_by_type(self):
        from utils import format_duration
        assert format_insight_text("chapters", [{"start": 0, "title": "X"}]) == f"{format_duration(0)}  X"

    def test_unknown_type_returns_empty_string(self):
        assert format_insight_text("thumb_title", [{"start": 0}]) == ""

    def test_non_list_data_returns_empty_string(self):
        """A raw-fallback string (unparsed LLM output) must not crash the
        formatter — it's simply not worth exporting."""
        assert format_insight_text("chapters", "raw unparsed text") == ""
