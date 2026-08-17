"""Unit tests for core/lm_client.py — no network, urllib is mocked."""

import json
import socket
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

    def test_connection_check_uses_authorization_header(self):
        client = LMStudioClient("https://api.example.com/v1", api_key="secret-key")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request") as mock_req:
            response = _fake_response({})
            response.status = 200
            mock_open.return_value = response
            assert client.check_connection()
            assert mock_req.call_args.kwargs["headers"]["Authorization"] == "Bearer secret-key"

    def test_loaded_model_check_uses_authorization_header(self):
        client = LMStudioClient("https://api.example.com/v1", api_key="secret-key")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request") as mock_req:
            mock_open.return_value = _fake_response({"data": [{"id": "model"}]})
            assert client.get_loaded_model() == "model"
            assert mock_req.call_args.kwargs["headers"]["Authorization"] == "Bearer secret-key"

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


class TestProbe:
    """probe() replaces check_connection()+get_loaded_model() at the call
    sites that need both, so it must answer in one request."""

    def test_returns_loaded_model_in_a_single_request(self):
        client = LMStudioClient("http://localhost:1234/v1")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request"):
            mock_open.return_value = _fake_response(
                {"data": [{"id": "google/gemma-4-12b"}]}
            )
            connected, detail = client.probe()

        assert (connected, detail) == (True, "google/gemma-4-12b")
        assert mock_open.call_count == 1

    def test_server_up_without_a_model_is_still_connected(self):
        client = LMStudioClient("http://localhost:1234/v1")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request"):
            mock_open.return_value = _fake_response({"data": []})
            assert client.probe() == (True, "")

    def test_unreachable_reports_the_error(self):
        client = LMStudioClient("http://localhost:1234/v1")
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            connected, detail = client.probe()

        assert connected is False
        assert "refused" in detail

    def test_sends_the_auth_header_when_a_key_is_set(self):
        client = LMStudioClient("http://localhost:1234/v1", api_key="secret-key")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request") as mock_req:
            mock_open.return_value = _fake_response({"data": []})
            client.probe()

        headers = mock_req.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer secret-key"


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


class _StalledIterator:
    """Simulates a connection that never produces a line — every read
    blocks until the socket-level timeout fires."""

    def __iter__(self):
        return self

    def __next__(self):
        raise socket.timeout("no data")


class TestStreamCancellation:
    """Regression coverage for the R1 gap: a single long socket timeout on
    the SSE read meant is_cancelled() was only checked between already-
    received lines, so Cancel did nothing for a stalled connection until
    the full request timeout eventually fired — however long that was."""

    def test_urlopen_uses_a_short_poll_timeout_not_the_full_one(self):
        client = LMStudioClient("http://localhost:1234/v1")
        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request"):
            mock_open.return_value = _fake_sse_response([])
            client.chat_completion_stream(
                [{"role": "user", "content": "hi"}], timeout=600,
            )
            # The socket-level timeout urlopen is given must be the short
            # poll window, not the caller's 600s budget — otherwise a
            # stalled connection blocks is_cancelled() checks for 600s.
            assert mock_open.call_args.kwargs["timeout"] <= 2.0

    def test_cancellation_during_a_stalled_stream_returns_promptly(self):
        client = LMStudioClient("http://localhost:1234/v1")
        resp = MagicMock()
        resp.__iter__.return_value = _StalledIterator()
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False

        calls = {"n": 0}

        def is_cancelled():
            calls["n"] += 1
            return calls["n"] >= 3

        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request"):
            mock_open.return_value = resp
            result = client.chat_completion_stream(
                [{"role": "user", "content": "hi"}],
                timeout=600,
                is_cancelled=is_cancelled,
            )

        assert result is None
        # Cancelled on the 3rd check — proves the poll loop rechecks
        # is_cancelled() every _STREAM_POLL_S rather than blocking for the
        # full 600s request timeout on a stalled connection.
        assert calls["n"] == 3

    def test_stalled_reads_do_not_raise_and_keep_polling(self):
        """A socket.timeout on an individual poll must not be treated as a
        stream error — only sustained stalling past the overall deadline
        (or cancellation) should end the call."""
        client = LMStudioClient("http://localhost:1234/v1")

        class _FlakyThenDone:
            def __init__(self, stalls: int, lines: list[bytes]):
                self._stalls = stalls
                self._lines = iter(lines)

            def __iter__(self):
                return self

            def __next__(self):
                if self._stalls > 0:
                    self._stalls -= 1
                    raise socket.timeout("no data yet")
                return next(self._lines)

        resp = MagicMock()
        resp.__iter__.return_value = _FlakyThenDone(
            stalls=3,
            lines=[
                b'data: {"choices": [{"delta": {"content": "hi"}}]}',
                b"data: [DONE]",
            ],
        )
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False

        with patch("urllib.request.urlopen") as mock_open, \
             patch("urllib.request.Request"):
            mock_open.return_value = resp
            result = client.chat_completion_stream(
                [{"role": "user", "content": "hi"}], timeout=600,
            )

        assert result == "hi"
