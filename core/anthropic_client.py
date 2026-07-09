"""
Whispered - Anthropic Messages API Client
Non-streaming HTTP client with the same interface as LMStudioClient's
chat_completion_stream, so InsightsWorker can use either provider.
"""

import json
import urllib.request
import urllib.error
from typing import Optional, Callable

from core.logger import get_logger

logger = get_logger(__name__)

DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TIMEOUT = 300


class AnthropicClient:
    """Client for the Anthropic Messages API (non-streaming)."""

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self._api_key = api_key
        self._model = model

    def chat_completion_stream(
        self,
        messages: list[dict],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: int = DEFAULT_TIMEOUT,
        on_token: Optional[Callable[[str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Optional[str]:
        """Send a non-streaming request; return the full response text.

        Same interface as LMStudioClient.chat_completion_stream so the two
        clients are interchangeable. on_token is accepted for interface
        parity but never called (Anthropic requests here are not streamed).
        Returns None on error/cancellation.
        """
        if is_cancelled and is_cancelled():
            return None

        system_parts = []
        user_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_parts.append(msg.get("content", ""))
            else:
                user_messages.append(msg)

        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": user_messages,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": DEFAULT_ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(DEFAULT_ANTHROPIC_URL, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                blocks = result.get("content", [])
                return "".join(
                    b.get("text", "") for b in blocks if b.get("type") == "text"
                )
        except urllib.error.URLError as exc:
            logger.debug("Anthropic connection error: %s", exc)
            return None
        except Exception as exc:
            logger.warning("Anthropic API error: %s", exc)
            return None
