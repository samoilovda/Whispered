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


_GAP_MARKER = "[... transcript continues, sampled for length ...]"


def sample_lines_evenly(
    lines: list[str],
    max_chars: int,
    keep_edges: int = 20,
    separator: str = "\n",
    gap_marker: str = _GAP_MARKER,
) -> str:
    """Join *lines* capped at *max_chars*, sampling evenly instead of
    truncating the tail.

    A long recording's last chapters/topics live at the end of the
    transcript; simply cutting off everything past *max_chars* (as
    ``fit_to_context`` does) meant the model never saw the back half of
    long recordings. This keeps the first and last *keep_edges* lines
    intact (they anchor the opening and closing context) and evenly
    samples the remaining middle lines to fill out the rest of the budget,
    so both ends of a long transcript stay visible.
    """
    joined = separator.join(lines)
    if len(joined) <= max_chars:
        return joined

    n = len(lines)
    edge = min(keep_edges, n // 2)
    head = lines[:edge]
    tail = lines[n - edge:] if edge else []
    middle = lines[edge:n - edge]

    fixed_parts = head + ([gap_marker] if middle else []) + tail
    fixed_len = len(separator.join(fixed_parts))
    budget = max_chars - fixed_len - len(separator) - len(gap_marker)

    selected_middle: list[str] = []
    if middle and budget > 0:
        lo, hi = 0, len(middle)
        best: list[str] = []
        while lo <= hi:
            count = (lo + hi) // 2
            if count == 0:
                candidate: list[str] = []
            else:
                step = max(1, len(middle) // count)
                candidate = middle[::step][:count]
            if len(separator.join(candidate)) <= budget:
                best = candidate
                lo = count + 1
            else:
                hi = count - 1
        selected_middle = best

    if selected_middle:
        parts = head + [gap_marker] + selected_middle + [gap_marker] + tail
    elif middle:
        parts = head + [gap_marker] + tail
    else:
        parts = head + tail

    result = separator.join(parts)
    if len(result) > max_chars:
        # Rounding safety net; should rarely trigger given the budget above.
        result = result[:max_chars]
    return result
