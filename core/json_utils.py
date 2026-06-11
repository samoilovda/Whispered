"""
Whispered - JSON / LLM-output parsing helpers
Robust extraction of JSON payloads from language-model responses, which often
wrap JSON in markdown code fences or surround it with prose. Centralized here so
every caller parses model output the same defensive way instead of duplicating
fragile ``response.split("```")[1]`` logic that throws IndexError on malformed
output.
"""

import json
import re
from typing import Any, Optional

from core.logger import get_logger

logger = get_logger(__name__)

# Matches a fenced code block, optionally tagged (```json ... ```).
_FENCE_RE = re.compile(
    r"```(?:[a-zA-Z0-9_-]+)?\s*\n?(.*?)```",
    re.DOTALL,
)


def strip_fences(text: str) -> str:
    """Return the contents of the first markdown code fence, or the input
    unchanged if there is no (closed) fence. Never raises on odd fences."""
    if not text:
        return ""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def extract_json(text: Optional[str], default: Any = None) -> Any:
    """Best-effort parse of a JSON object/array from a model response.

    Strategy (each step falls back to the next):
      1. Parse the whole (fence-stripped) string.
      2. Parse the substring between the first ``{``/``[`` and its matching
         closing bracket.
    Returns ``default`` instead of raising when nothing parses.
    """
    if not text:
        return default

    candidate = strip_fences(text)

    # 1. Try the stripped candidate directly.
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Try to isolate the outermost JSON object or array.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = candidate.find(open_ch)
        end = candidate.rfind(close_ch)
        if start != -1 and end > start:
            snippet = candidate[start:end + 1]
            try:
                return json.loads(snippet)
            except (json.JSONDecodeError, ValueError):
                continue

    logger.debug("extract_json: no parseable JSON found in response")
    return default
