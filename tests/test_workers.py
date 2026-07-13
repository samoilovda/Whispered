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

    def test_extracts_array_from_prose(self):
        raw = 'Here is the list: [{"x":2}] done.'
        assert _parse_json_response(raw) == [{"x": 2}]

    def test_returns_none_on_garbage(self):
        assert _parse_json_response("not json at all") is None

    def test_empty_array(self):
        assert _parse_json_response("[]") == []


# ── chat_worker helpers ───────────────────────────────────────────────────────

from core.chat_worker import _build_system_prompt, _CONTEXT_CHARS


class TestBuildSystemPrompt:
    def test_short_transcript_unchanged(self):
        prompt = _build_system_prompt("hello world")
        assert "hello world" in prompt
        assert "[truncated" not in prompt

    def test_long_transcript_truncated(self):
        long = "x" * (_CONTEXT_CHARS + 1000)
        prompt = _build_system_prompt(long)
        assert len(prompt) < len(long) + 500  # some overhead from template
        assert "[truncated" in prompt.lower() or "truncated" in prompt

    def test_system_template_present(self):
        prompt = _build_system_prompt("test")
        assert "TRANSCRIPTION" in prompt

    def test_custom_max_chars(self):
        prompt = _build_system_prompt("a" * 1000, max_chars=100)
        assert "truncated" in prompt.lower()
