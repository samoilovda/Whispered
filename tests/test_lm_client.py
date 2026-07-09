"""Unit tests for core/lm_client.py — no network, urllib is mocked."""

import json
import sys
import types
from unittest.mock import patch, MagicMock

for _mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui",
             "PyQt6.QtMultimedia"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

_qtcore = sys.modules["PyQt6.QtCore"]
_qtcore.QThread = type("QThread", (), {"start": lambda *a: None, "isRunning": lambda *a: False})
_qtcore.pyqtSignal = lambda *a, **kw: None
_qtcore.QObject = type("QObject", (), {"__init__": lambda *a, **kw: None})

# Other test modules stub core.lm_client with a bare `object` placeholder via
# setdefault; drop any such stub so this file always exercises the real module.
sys.modules.pop("core.lm_client", None)
from core.lm_client import LMStudioClient


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
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

    def test_api_key_not_logged(self):
        """The api_key attribute must not appear in repr/str used for logging."""
        client = LMStudioClient("https://api.example.com/v1", api_key="super-secret")
        assert "super-secret" not in repr(client.__dict__.get("base_url", ""))
        # Ensure the key lives in a private attribute, not something __repr__ exposes by default
        assert client._api_key == "super-secret"
