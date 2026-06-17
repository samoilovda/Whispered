"""
Whispered – LLM text utilities
Shared helpers for preparing text before sending to an LLM.
"""

from __future__ import annotations

_DEFAULT_TRUNCATION_MARKER = "\n\n[Transcript truncated due to context limit]"


def fit_to_context(
    text: str,
    max_chars: int,
    marker: str = _DEFAULT_TRUNCATION_MARKER,
) -> str:
    """Truncate *text* to *max_chars*, appending *marker* when truncation occurs.

    The marker itself is counted toward *max_chars*, so the result never
    exceeds the limit regardless of marker length.
    """
    if len(text) <= max_chars:
        return text
    cutoff = max_chars - len(marker)
    if cutoff <= 0:
        return marker[:max_chars]
    return text[:cutoff] + marker
