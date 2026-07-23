"""Shared text preparation helpers for LLM-facing workflows."""

from __future__ import annotations

from collections.abc import Sequence


_GAP_MARKER = "[... transcript continues, sampled for length ...]"


def sample_lines_evenly(
    lines: list[str],
    max_chars: int,
    keep_edges: int = 20,
    separator: str = "\n",
    gap_marker: str = _GAP_MARKER,
) -> str:
    """Join *lines* within ``max_chars`` while retaining both document ends."""
    joined = separator.join(lines)
    if len(joined) <= max_chars:
        return joined

    count = len(lines)
    edge = min(keep_edges, count // 2)
    head = lines[:edge]
    tail = lines[count - edge:] if edge else []
    middle = lines[edge:count - edge]

    fixed_parts = head + ([gap_marker] if middle else []) + tail
    budget = max_chars - len(separator.join(fixed_parts)) - len(separator) - len(gap_marker)
    selected_middle: list[str] = []
    if middle and budget > 0:
        low, high = 0, len(middle)
        while low <= high:
            selected_count = (low + high) // 2
            if selected_count:
                step = max(1, len(middle) // selected_count)
                candidate = middle[::step][:selected_count]
            else:
                candidate = []
            if len(separator.join(candidate)) <= budget:
                selected_middle = candidate
                low = selected_count + 1
            else:
                high = selected_count - 1

    if selected_middle:
        parts = head + [gap_marker] + selected_middle + [gap_marker] + tail
    elif middle:
        parts = head + [gap_marker] + tail
    else:
        parts = head + tail
    return separator.join(parts)[:max_chars]


def split_into_chunks(
    text: str,
    chunk_size: int,
    overlap: int = 0,
    separators: Sequence[str] = ("\n\n", "\n", ". ", ".\n", "? ", "! ", " "),
    boundary_window: int = 500,
    *,
    strip: bool = True,
) -> list[str]:
    """Split text into overlapping, readable chunks without losing progress.

    ``separators`` are considered in order near the right edge of each chunk.
    The forward-progress guard also makes unusual overlap/boundary combinations
    safe instead of looping forever.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if len(text) <= chunk_size:
        return [text.strip() if strip else text]
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            search_start = max(start, end - max(0, boundary_window))
            for separator in separators:
                position = text.rfind(separator, search_start, end)
                if position > start:
                    end = position + len(separator)
                    break
        chunk = text[start:end]
        prepared = chunk.strip() if strip else chunk
        if prepared:
            chunks.append(prepared)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)

    return chunks
