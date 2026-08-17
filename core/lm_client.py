"""
Whispered - LM Studio Client
HTTP client for the LM Studio OpenAI-compatible API.
Extracted from text_processor.py for reuse across modules.
"""

import json
import socket
import time
import urllib.request
import urllib.error
import functools
import threading
from typing import Optional, Callable

from core.logger import get_logger

logger = get_logger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TIMEOUT = 300  # 5 minutes for long texts

# The socket-level timeout used while polling an SSE stream for the next
# line. is_cancelled() is only checked between successfully read lines —
# on a stalled connection (server accepted the request but is producing no
# tokens, e.g. a reasoning model mid-"thinking") a single long socket
# timeout means Cancel does nothing until that timeout finally fires,
# however long DEFAULT_TIMEOUT/the caller's timeout is. Polling with a
# short socket timeout and rechecking is_cancelled() between attempts
# bounds that to roughly _STREAM_POLL_S regardless of server behavior.
_STREAM_POLL_S = 2.0

# LM Studio can deadlock or become unresponsive when several long requests
# prefill concurrently.  The application deliberately has one process-wide
# lane for local completions; cloud providers are not affected.
_COMPLETION_SLOT = threading.RLock()


def _serialized_completion(method):
    """Run a local completion in the single shared LM Studio lane.

    Waiting is cancellable, so closing a panel does not leave its worker
    queued behind a long unrelated generation.
    """
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        cancelled = kwargs.get("is_cancelled") or getattr(self, "is_cancelled", None)
        while not _COMPLETION_SLOT.acquire(timeout=0.1):
            if cancelled and cancelled():
                return None
        try:
            if cancelled and cancelled():
                return None
            return method(self, *args, **kwargs)
        finally:
            _COMPLETION_SLOT.release()
    return wrapped


# ============================================================================
# CLIENT
# ============================================================================

class LMStudioClient:
    """Client for communicating with LM Studio's OpenAI-compatible API."""

    def __init__(
        self,
        base_url: str = DEFAULT_LM_STUDIO_URL,
        api_key: str = "",
        model: str = "",
    ) -> None:
        self.base_url: str = base_url.rstrip('/')
        self._api_key = api_key
        self._model = model
        self._cached_model: Optional[str] = None
        self.is_cancelled: Optional[Callable[[], bool]] = None

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    def check_connection(self, timeout: float = 5) -> bool:
        """Check if LM Studio server is running and accessible."""
        try:
            req = urllib.request.Request(
                f"{self.base_url}/models", headers=self._auth_headers()
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.status == 200
        except Exception:
            return False

    def get_loaded_model(self, timeout: float = 5) -> Optional[str]:
        """Get the currently loaded model name."""
        try:
            req = urllib.request.Request(
                f"{self.base_url}/models", headers=self._auth_headers()
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode('utf-8'))
                models = data.get('data', [])
                if models:
                    self._cached_model = models[0].get('id', 'Unknown')
                    return self._cached_model
        except Exception:
            pass
        return None

    def probe(self, timeout: float = 5) -> tuple[bool, str]:
        """Reachability and loaded-model name in a single round trip.

        Returns ``(True, model_name)`` when the server answers, otherwise
        ``(False, error_message)``.  Prefer this over calling
        :meth:`check_connection` and :meth:`get_loaded_model` back to back —
        those hit ``/models`` twice and can disagree if the server changes
        state between the two calls.
        """
        try:
            req = urllib.request.Request(
                f"{self.base_url}/models", headers=self._auth_headers()
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode('utf-8'))
        except Exception as exc:
            return False, str(exc)

        models = data.get('data', [])
        if models:
            self._cached_model = models[0].get('id', 'Unknown')
            return True, self._cached_model
        return True, ""

    @_serialized_completion
    def complete(
        self,
        messages: list[dict],
        *,
        stream: bool = True,
        on_token: Optional[Callable[[str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Optional[str]:
        """Unified chat completion.

        Returns the full response text, ``""`` for a valid empty response,
        or ``None`` on network error / cancellation.

        Parameters
        ----------
        stream:       Use SSE streaming (default True).  Non-streaming blocks
                      until the full response arrives; no token callbacks.
        on_token:     Called for each SSE token when stream=True.
        is_cancelled: Callable returning True when the caller wants to abort.
                      Checked between SSE chunks (streaming) or before the
                      request (non-streaming).
        """
        if stream:
            return self.chat_completion_stream(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
                on_token=on_token,
                is_cancelled=is_cancelled,
            )
        # Non-streaming path — no ThreadPoolExecutor needed; cancellation is
        # checked before sending since urllib has no mid-request abort.
        if is_cancelled and is_cancelled():
            return None
        endpoint = f"{self.base_url}/chat/completions"
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self._model:
            payload["model"] = self._model
        try:
            data = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json", **self._auth_headers()}
            req = urllib.request.Request(endpoint, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"]
        except urllib.error.URLError as exc:
            logger.debug("LM Studio connection error: %s", exc)
            return None
        except Exception as exc:
            logger.warning("LM Studio API error: %s", exc)
            return None

    @_serialized_completion
    def chat_completion(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: int = DEFAULT_TIMEOUT
    ) -> Optional[str]:
        """Non-streaming chat completion (legacy interface).

        Prefer ``complete()`` for new code.  Cancellation via the
        ``self.is_cancelled`` attribute is supported for backwards
        compatibility with callers that set it externally.
        """
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self.complete(
            messages,
            stream=False,
            is_cancelled=self.is_cancelled,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    @_serialized_completion
    def chat_completion_stream(
        self,
        messages: list[dict],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: int = DEFAULT_TIMEOUT,
        on_token: Optional[Callable[[str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Optional[str]:
        """Send a streaming chat completion; call on_token for each delta.

        Returns the full accumulated response, or None on error/cancel.
        Uses SSE parsing (``data: {...}`` lines) as emitted by LM Studio.
        """
        endpoint = f"{self.base_url}/chat/completions"
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if self._model:
            payload["model"] = self._model
        try:
            data = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json", **self._auth_headers()}
            req = urllib.request.Request(endpoint, data=data, headers=headers)
            full_text = []
            # A short poll timeout (not the caller's full `timeout`) so a
            # stalled server can't keep is_cancelled() from being checked
            # for the whole request duration — see _STREAM_POLL_S.
            poll_timeout = min(_STREAM_POLL_S, timeout) if timeout else _STREAM_POLL_S
            deadline = time.monotonic() + timeout
            with urllib.request.urlopen(req, timeout=poll_timeout) as resp:
                line_iter = iter(resp)
                while True:
                    if is_cancelled and is_cancelled():
                        return None
                    if time.monotonic() > deadline:
                        logger.warning(
                            "LM Studio stream exceeded overall timeout of %ds", timeout
                        )
                        return None
                    try:
                        raw_line = next(line_iter)
                    except StopIteration:
                        break
                    except (socket.timeout, TimeoutError):
                        # No data within the poll window — loop back to
                        # recheck cancellation/deadline, not an error.
                        continue
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                        choice = obj["choices"][0]
                        delta = choice["delta"].get("content", "")
                        if delta:
                            full_text.append(delta)
                            if on_token:
                                on_token(delta)
                        if choice.get("finish_reason") == "length":
                            logger.warning(
                                "LM Studio response truncated by max_tokens=%d", max_tokens
                            )
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
            return "".join(full_text)
        except urllib.error.URLError as exc:
            logger.debug("LM Studio stream error: %s", exc)
            return None
        except Exception as exc:
            logger.warning("LM Studio stream API error: %s", exc)
            return None
