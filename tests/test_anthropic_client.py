"""Unit tests for core/anthropic_client.py — no network, urllib is mocked."""

import json
import sys
from unittest.mock import patch, MagicMock

# PyQt6 stand-ins come from tests/conftest.py.
sys.modules.pop("core.lm_client", None)
from core.anthropic_client import AnthropicClient


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestAnthropicClient:
    def test_system_message_extracted_to_top_level(self):
        client = AnthropicClient(api_key="key", model="claude-sonnet-5")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request") as mock_req:
            mock_open.return_value = _fake_response(
                {"content": [{"type": "text", "text": "hi"}]}
            )
            messages = [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
            ]
            client.chat_completion_stream(messages)
            payload = json.loads(mock_req.call_args.kwargs["data"].decode("utf-8"))
            assert payload["system"] == "You are helpful."
            assert payload["messages"] == [{"role": "user", "content": "Hello"}]
            assert not any(m.get("role") == "system" for m in payload["messages"])

    def test_no_system_message_omits_system_field(self):
        client = AnthropicClient(api_key="key", model="claude-sonnet-5")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request") as mock_req:
            mock_open.return_value = _fake_response(
                {"content": [{"type": "text", "text": "hi"}]}
            )
            client.chat_completion_stream([{"role": "user", "content": "Hello"}])
            payload = json.loads(mock_req.call_args.kwargs["data"].decode("utf-8"))
            assert "system" not in payload

    def test_text_blocks_concatenated(self):
        client = AnthropicClient(api_key="key", model="claude-sonnet-5")
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(
                {"content": [
                    {"type": "text", "text": "Hello, "},
                    {"type": "text", "text": "world!"},
                ]}
            )
            result = client.chat_completion_stream([{"role": "user", "content": "Hi"}])
            assert result == "Hello, world!"

    def test_non_text_blocks_ignored(self):
        client = AnthropicClient(api_key="key", model="claude-sonnet-5")
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(
                {"content": [
                    {"type": "text", "text": "answer"},
                    {"type": "tool_use", "name": "foo", "input": {}},
                ]}
            )
            result = client.chat_completion_stream([{"role": "user", "content": "Hi"}])
            assert result == "answer"

    def test_cancelled_before_send_returns_none(self):
        client = AnthropicClient(api_key="key", model="claude-sonnet-5")
        with patch("urllib.request.urlopen") as mock_open:
            result = client.chat_completion_stream(
                [{"role": "user", "content": "Hi"}], is_cancelled=lambda: True
            )
            assert result is None
            mock_open.assert_not_called()

    def test_headers_include_key_and_version(self):
        client = AnthropicClient(api_key="my-secret-key", model="claude-sonnet-5")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request") as mock_req:
            mock_open.return_value = _fake_response(
                {"content": [{"type": "text", "text": "hi"}]}
            )
            client.chat_completion_stream([{"role": "user", "content": "Hi"}])
            headers = mock_req.call_args.kwargs["headers"]
            assert headers["x-api-key"] == "my-secret-key"
            assert headers["anthropic-version"] == "2023-06-01"

    def test_network_error_returns_none(self):
        import urllib.error
        client = AnthropicClient(api_key="key", model="claude-sonnet-5")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            result = client.chat_completion_stream([{"role": "user", "content": "Hi"}])
            assert result is None

    def test_stop_reason_max_tokens_logs_warning(self, caplog):
        client = AnthropicClient(api_key="key", model="claude-sonnet-5")
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(
                {"content": [{"type": "text", "text": "cut off"}], "stop_reason": "max_tokens"}
            )
            with caplog.at_level("WARNING"):
                result = client.chat_completion_stream(
                    [{"role": "user", "content": "Hi"}], max_tokens=8000,
                )
            assert result == "cut off"
            assert any("truncated" in r.message for r in caplog.records)

    def test_stop_reason_end_turn_no_warning(self, caplog):
        client = AnthropicClient(api_key="key", model="claude-sonnet-5")
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = _fake_response(
                {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}
            )
            with caplog.at_level("WARNING"):
                result = client.chat_completion_stream([{"role": "user", "content": "Hi"}])
            assert result == "done"
            assert not any("truncated" in r.message for r in caplog.records)
