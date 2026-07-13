"""Unit tests for core/lm_client.py — no network, urllib is mocked."""

import json
import sys
from unittest.mock import patch, MagicMock

# Qt stand-ins come from tests/conftest.py, which also installs a bare
# `object` placeholder for core.lm_client; drop it so this file always
# exercises the real module.
sys.modules.pop("core.lm_client", None)
from core.lm_client import LMStudioClient


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _fake_sse_response(chunks: list[dict]):
    """Build a fake streaming response: iterating it yields SSE `data:` lines."""
    lines = [
        f"data: {json.dumps(chunk)}".encode("utf-8") for chunk in chunks
    ] + [b"data: [DONE]"]
    resp = MagicMock()
    resp.__iter__.return_value = iter(lines)
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestApiKeyAndModel:
    def test_no_api_key_no_authorization_header(self):
        client = LMStudioClient("http://localhost:1234/v1")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request") as mock_req:
            mock_open.return_value = _fake_response(
                {"choices": [{"message": {"content": "hi"}}]}
            )
            client.complete([{"role": "user", "content": "hi"}], stream=False)
            headers = mock_req.call_args.kwargs["headers"]
            assert "Authorization" not in headers

    def test_api_key_adds_authorization_header(self):
        client = LMStudioClient("https://api.example.com/v1", api_key="secret-key")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request") as mock_req:
            mock_open.return_value = _fake_response(
                {"choices": [{"message": {"content": "hi"}}]}
            )
            client.complete([{"role": "user", "content": "hi"}], stream=False)
            headers = mock_req.call_args.kwargs["headers"]
            assert headers["Authorization"] == "Bearer secret-key"

    def test_model_added_to_payload(self):
        client = LMStudioClient("https://api.example.com/v1", model="gpt-4o-mini")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request") as mock_req:
            mock_open.return_value = _fake_response(
                {"choices": [{"message": {"content": "hi"}}]}
            )
            client.complete([{"role": "user", "content": "hi"}], stream=False)
            payload = json.loads(mock_req.call_args.kwargs["data"].decode("utf-8"))
            assert payload["model"] == "gpt-4o-mini"

    def test_no_model_no_model_field(self):
        client = LMStudioClient("http://localhost:1234/v1")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request") as mock_req:
            mock_open.return_value = _fake_response(
                {"choices": [{"message": {"content": "hi"}}]}
            )
            client.complete([{"role": "user", "content": "hi"}], stream=False)
            payload = json.loads(mock_req.call_args.kwargs["data"].decode("utf-8"))
            assert "model" not in payload

    def test_api_key_not_logged(self, caplog):
        """A request that errors out must not leak the API key into logs.

        Exercises the real logging path (not just attribute inspection):
        every request method is driven with a failing urlopen, at DEBUG
        level so nothing is filtered out, and every captured record's
        rendered message is checked for the secret.
        """
        secret = "super-secret-api-key-value"
        client = LMStudioClient("https://api.example.com/v1", api_key=secret)

        with caplog.at_level("DEBUG"):
            with patch("urllib.request.urlopen", side_effect=Exception("boom")):
                client.complete([{"role": "user", "content": "hi"}], stream=False)
                client.complete([{"role": "user", "content": "hi"}], stream=True)
                client.chat_completion_stream([{"role": "user", "content": "hi"}])
                client.chat_completion("hi")
                client.check_connection()
                client.get_loaded_model()

        assert caplog.records, "expected the failing calls to log something"
        for record in caplog.records:
            assert secret not in record.getMessage()


class TestTruncationDetection:
    def test_finish_reason_length_logs_warning(self, caplog):
        client = LMStudioClient("http://localhost:1234/v1")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request"):
            mock_open.return_value = _fake_sse_response([
                {"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": " text"}, "finish_reason": "length"}]},
            ])
            with caplog.at_level("WARNING"):
                result = client.chat_completion_stream(
                    [{"role": "user", "content": "hi"}], max_tokens=8000,
                )
            assert result == "partial text"
            assert any("truncated" in r.message for r in caplog.records)

    def test_finish_reason_stop_no_warning(self, caplog):
        client = LMStudioClient("http://localhost:1234/v1")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request"):
            mock_open.return_value = _fake_sse_response([
                {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]},
            ])
            with caplog.at_level("WARNING"):
                result = client.chat_completion_stream(
                    [{"role": "user", "content": "hi"}],
                )
            assert result == "done"
            assert not any("truncated" in r.message for r in caplog.records)
