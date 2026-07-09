"""
Whispered - AI Provider Factory
Selects between local LM Studio and cloud (OpenAI-compatible / Anthropic)
clients for the YouTube key-questions feature. Feature-scoped: does not
affect any other AI functionality, which stays on LM Studio.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProviderSettings:
    kind: str            # "lmstudio" | "openai" | "anthropic"
    base_url: str = ""
    api_key: str = ""
    model: str = ""


def create_client(ps: ProviderSettings):
    """Return a client with a chat_completion_stream(...) method for ps.kind."""
    if ps.kind == "anthropic":
        from core.anthropic_client import AnthropicClient
        return AnthropicClient(api_key=ps.api_key, model=ps.model)
    from core.lm_client import LMStudioClient
    return LMStudioClient(base_url=ps.base_url, api_key=ps.api_key, model=ps.model)


def provider_from_config(cfg) -> ProviderSettings:
    """Build ProviderSettings for the YouTube feature from Config fields."""
    kind = cfg.yt_provider
    if kind == "anthropic":
        return ProviderSettings(
            kind="anthropic",
            api_key=cfg.yt_anthropic_api_key,
            model=cfg.yt_anthropic_model,
        )
    if kind == "openai":
        return ProviderSettings(
            kind="openai",
            base_url=cfg.yt_openai_base_url,
            api_key=cfg.yt_openai_api_key,
            model=cfg.yt_openai_model,
        )
    return ProviderSettings(kind="lmstudio", base_url=cfg.lm_studio_url)
