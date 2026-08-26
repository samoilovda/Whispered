"""Unit tests for worker pure-logic helpers (no Qt required)."""

# Qt and core.lm_client/core.ai_worker stand-ins come from tests/conftest.py.

# ── insights_worker helpers ───────────────────────────────────────────────────

from core.insights_worker import _strip_json_fences, _parse_json_response


class TestStripJsonFences:
    def test_no_fences(self):
        assert _strip_json_fences('[{"a":1}]') == '[{"a":1}]'

    def test_strips_json_fence(self):
        raw = "```json\n[{\"a\":1}]\n```"
        assert _strip_json_fences(raw) == '[{"a":1}]'

    def test_strips_plain_fence(self):
        raw = "```\n[1,2,3]\n```"
        assert _strip_json_fences(raw) == "[1,2,3]"

    def test_preserves_inner_content(self):
        raw = "```json\n[{\"key\": \"val```ue\"}]\n```"
        result = _strip_json_fences(raw)
        assert "val```ue" in result


class TestParseJsonResponse:
    def test_valid_json_array(self):
        result = _parse_json_response('[{"a":1}]')
        assert result == [{"a": 1}]

    def test_json_with_fences(self):
        raw = '```json\n[{"a":1}]\n```'
        assert _parse_json_response(raw) == [{"a": 1}]

    def test_unwraps_an_object_wrapping_the_array(self):
        """Models routinely answer an "return a JSON array" prompt with
        {"chapters": [...]} instead. That parses fine, so the dict used to
        be handed back as the insight list and the panel rendered its keys
        as items."""
        raw = '{"chapters": [{"time": "00:00", "title": "Intro"}]}'
        assert _parse_json_response(raw) == [{"time": "00:00", "title": "Intro"}]

    def test_object_with_no_list_inside_is_a_parse_failure(self):
        """An object that carries no array at all is not guessable — it
        routes into the caller's existing "ask again for just the JSON
        array" retry instead of being returned as-is."""
        assert _parse_json_response('{"a": 1, "b": 2}') is None

    def test_extracts_array_from_prose(self):
        raw = 'Here is the list: [{"x":2}] done.'
        assert _parse_json_response(raw) == [{"x": 2}]

    def test_returns_none_on_garbage(self):
        assert _parse_json_response("not json at all") is None

    def test_empty_array(self):
        assert _parse_json_response("[]") == []


class TestTruncatedJsonSalvage:
    """A reasoning model that spends its max_tokens budget mid-array leaves
    no closing `]`, so plain parsing and the `[...]` regex both fail. Every
    insight it did finish used to be discarded with it."""

    def test_keeps_complete_strings_from_a_cut_off_array(self):
        raw = '["first question?", "second question?", "third but cut o'
        assert _parse_json_response(raw) == [
            "first question?",
            "second question?",
        ]

    def test_keeps_complete_objects_from_a_cut_off_array(self):
        raw = '[{"time": 0, "title": "Intro"}, {"time": 61, "title": "Mid'
        assert _parse_json_response(raw) == [{"time": 0, "title": "Intro"}]

    def test_salvage_survives_a_fenced_cut_off_array(self):
        raw = '```json\n["kept one", "kept two", "cut'
        assert _parse_json_response(raw) == ["kept one", "kept two"]

    def test_cut_off_before_any_complete_item_is_still_a_failure(self):
        assert _parse_json_response('["only a partial ite') is None

    def test_prose_without_an_array_is_still_a_failure(self):
        assert _parse_json_response("not json at all") is None


# ── chat_worker helpers ───────────────────────────────────────────────────────

from core.chat_worker import _build_system_prompt, _CONTEXT_CHARS
from core.i18n import tr


class TestBuildSystemPrompt:
    def test_short_transcript_unchanged(self):
        prompt = _build_system_prompt("hello world")
        assert "hello world" in prompt
        assert tr("chat_transcript_truncated") not in prompt

    def test_long_transcript_truncated(self):
        long = "x" * (_CONTEXT_CHARS + 1000)
        prompt = _build_system_prompt(long)
        assert len(prompt) < len(long) + 500  # some overhead from template
        assert tr("chat_transcript_truncated") in prompt

    def test_system_template_present(self):
        prompt = _build_system_prompt("test")
        template_prefix = tr("chat_system_prompt", transcript="").split("\n", 1)[0]
        assert template_prefix in prompt

    def test_custom_max_chars(self):
        prompt = _build_system_prompt("a" * 1000, max_chars=100)
        assert tr("chat_transcript_truncated") in prompt
