"""Unit tests for core/ai_provider.py — no network, no Qt required."""

import sys
from dataclasses import dataclass

import pytest

# PyQt6 stand-ins come from tests/conftest.py.
sys.modules.pop("core.lm_client", None)
from core.ai_provider import ProviderSettings, create_client, provider_from_config
from core.anthropic_client import AnthropicClient


@dataclass
class _FakeConfig:
    lm_studio_url: str = "http://localhost:1234/v1"
    yt_provider: str = "lmstudio"
    yt_openai_base_url: str = "https://api.openai.com/v1"
    yt_openai_api_key: str = ""
    yt_openai_model: str = "gpt-4o-mini"
    yt_anthropic_api_key: str = ""
    yt_anthropic_model: str = "claude-sonnet-5"


@pytest.fixture(autouse=True)
def _real_lm_client_class(monkeypatch):
    # Other test modules stub core.lm_client with a bare `object` placeholder;
    # force a fresh import of the real module for every test in this file,
    # since core.ai_provider.create_client() imports it lazily by name.
    #
    # Uses monkeypatch (not a manual pop) so sys.modules is restored to
    # whatever it held before this test regardless of outcome. A manual
    # `sys.modules.pop(...)` in a teardown line runs only on the clean path
    # and, worse, leaves the entry permanently *absent* afterwards — the
    # next test file's lazy `from core.lm_client import LMStudioClient`
    # would then import the real module fresh and hit the network for
    # real (this previously caused the suite to hang against a live LM
    # Studio server instead of failing fast).
    monkeypatch.delitem(sys.modules, "core.lm_client", raising=False)
    import core.lm_client as real_module
    return real_module.LMStudioClient


class TestCreateClient:
    def test_lmstudio_kind_returns_lmstudio_client(self, _real_lm_client_class):
        client = create_client(ProviderSettings(kind="lmstudio", base_url="http://localhost:1234/v1"))
        assert isinstance(client, _real_lm_client_class)

    def test_openai_kind_returns_lmstudio_client(self, _real_lm_client_class):
        client = create_client(ProviderSettings(
            kind="openai", base_url="https://api.openai.com/v1", api_key="k", model="gpt-4o-mini"
        ))
        assert isinstance(client, _real_lm_client_class)
        assert client._api_key == "k"
        assert client._model == "gpt-4o-mini"

    def test_anthropic_kind_returns_anthropic_client(self):
        client = create_client(ProviderSettings(kind="anthropic", api_key="k", model="claude-sonnet-5"))
        assert isinstance(client, AnthropicClient)
        assert client._api_key == "k"
        assert client._model == "claude-sonnet-5"


class TestProviderFromConfig:
    def test_default_is_lmstudio(self):
        cfg = _FakeConfig()
        ps = provider_from_config(cfg)
        assert ps.kind == "lmstudio"
        assert ps.base_url == "http://localhost:1234/v1"
        assert ps.api_key == ""

    def test_openai_maps_fields(self):
        cfg = _FakeConfig(
            yt_provider="openai",
            yt_openai_base_url="https://api.openai.com/v1",
            yt_openai_api_key="sk-x",
            yt_openai_model="gpt-4o-mini",
        )
        ps = provider_from_config(cfg)
        assert ps.kind == "openai"
        assert ps.base_url == "https://api.openai.com/v1"
        assert ps.api_key == "sk-x"
        assert ps.model == "gpt-4o-mini"

    def test_anthropic_maps_fields(self):
        cfg = _FakeConfig(
            yt_provider="anthropic",
            yt_anthropic_api_key="ak-x",
            yt_anthropic_model="claude-sonnet-5",
        )
        ps = provider_from_config(cfg)
        assert ps.kind == "anthropic"
        assert ps.api_key == "ak-x"
        assert ps.model == "claude-sonnet-5"
