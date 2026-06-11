"""
Whispered - LM Studio Client
HTTP client for the LM Studio OpenAI-compatible API.
Extracted from text_processor.py for reuse across modules.
"""

import json
import concurrent.futures
import urllib.request
import urllib.error
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


# ============================================================================
# CLIENT
# ============================================================================

class LMStudioClient:
    """Client for communicating with LM Studio's OpenAI-compatible API."""

    def __init__(self, base_url: str = DEFAULT_LM_STUDIO_URL) -> None:
        self.base_url: str = base_url.rstrip('/')
        self._cached_model: Optional[str] = None
        self.is_cancelled: Optional[Callable[[], bool]] = None

    def check_connection(self) -> bool:
        """Check if LM Studio server is running and accessible."""
        try:
            req = urllib.request.Request(f"{self.base_url}/models")
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status == 200
        except Exception:
            return False

    def get_loaded_model(self) -> Optional[str]:
        """Get the currently loaded model name."""
        try:
            req = urllib.request.Request(f"{self.base_url}/models")
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                models = data.get('data', [])
                if models:
                    self._cached_model = models[0].get('id', 'Unknown')
                    return self._cached_model
        except Exception:
            pass
        return None

    def chat_completion(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout: int = DEFAULT_TIMEOUT
    ) -> Optional[str]:
        """
        Send a chat completion request to LM Studio.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system prompt for context.
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature (0-1).
            timeout: Request timeout in seconds.

        Returns:
            The model's response text, or None on error.
        """
        endpoint = f"{self.base_url}/chat/completions"

        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False
        }

        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                endpoint,
                data=data,
                headers={'Content-Type': 'application/json'}
            )

            def do_request() -> str:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    return result['choices'][0]['message']['content']

            # No cancellation callback: run the request inline.
            if self.is_cancelled is None:
                return do_request()

            # Cancellable path: run the blocking request on a worker thread and
            # poll the cancel callback. On cancel we shut the executor down
            # WITHOUT waiting (shutdown(wait=False)) so this method returns
            # promptly instead of blocking on the in-flight HTTP request.
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(do_request)
                while True:
                    if self.is_cancelled():
                        return None
                    try:
                        return future.result(timeout=0.1)
                    except concurrent.futures.TimeoutError:
                        continue
            finally:
                # Do not block on the worker if we are abandoning it.
                executor.shutdown(wait=False)

        except urllib.error.URLError as e:
            logger.debug("LM Studio connection error: %s", e)
            return None
        except Exception as e:
            logger.warning("LM Studio API error: %s", e)
            return None
