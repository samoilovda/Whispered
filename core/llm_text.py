"""Shared text preparation helpers for LLM-facing workflows."""

from __future__ import annotations

import re
from collections.abc import Sequence


_GAP_MARKER = "[... transcript continues, sampled for length ...]"

_CITE_RE = re.compile(r"\[((?:\d{1,2}:)?\d{1,2}:\d{2})\]")


def clock(seconds: float) -> str:
    """"12:34" or "1:02:03" — the citation format the chat asks for."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def timestamped_blocks(segments: Sequence, block_seconds: int = 25) -> list[str]:
    """The transcript as ~*block_seconds* blocks, each led by its start
    time — "[12:34] text" (with "Speaker: " when known). One marker per
    block keeps every word while leaving the model something to cite."""
    lines: list[str] = []
    start: float | None = None
    speaker = ""
    texts: list[str] = []

    def flush() -> None:
        if texts and start is not None:
            prefix = f"[{clock(start)}] " + (f"{speaker}: " if speaker else "")
            lines.append(prefix + " ".join(texts))

    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        seg_speaker = seg.speaker or ""
        if start is None or seg_speaker != speaker or seg.start - start >= block_seconds:
            flush()
            start, speaker, texts = seg.start, seg_speaker, [text]
        else:
            texts.append(text)
    flush()
    return lines


def cited_seconds(stamp: str) -> int:
    """Seconds of a cited "12:34" / "1:02:03"."""
    parts = [int(p) for p in stamp.split(":")]
    total = 0
    for part in parts:
        total = total * 60 + part
    return total


def link_citations(markdown: str, verify=None) -> str:
    """Turn "[12:34]" citations into markdown links "[12:34](seek:754)"
    the chat bubble can make clickable. Existing links are left alone.

    With *verify* (seconds → bool), only a citation it confirms becomes a
    link; any other keeps its text with a "(?)" so it never looks like a
    checked source (UX-13)."""
    def replace(m: "re.Match[str]") -> str:
        if markdown[m.end():m.end() + 1] == "(":
            return m.group(0)
        seconds = cited_seconds(m.group(1))
        if verify is not None and not verify(seconds):
            return f"{m.group(0)} (?)"
        return f"[{m.group(1)}](seek:{seconds})"

    return _CITE_RE.sub(replace, markdown)


def block_start_seconds(line: str) -> "int | None":
    """Start of a ``timestamped_blocks`` line ("[12:34] …"), in seconds."""
    match = _CITE_RE.match(line)
    return cited_seconds(match.group(1)) if match else None


def sample_lines_evenly(
    lines: list[str],
    max_chars: int,
    keep_edges: int = 20,
    separator: str = "\n",
    gap_marker: str = _GAP_MARKER,
) -> str:
    """Join *lines* within ``max_chars`` while retaining both document ends."""
    return _sample_lines(lines, max_chars, keep_edges, separator, gap_marker)[0]


def sampled_line_indices(
    lines: list[str],
    max_chars: int,
    keep_edges: int = 20,
    separator: str = "\n",
    gap_marker: str = _GAP_MARKER,
) -> list[int]:
    """Which of *lines* ``sample_lines_evenly`` keeps (all of them when the
    text fits) — what the chat shows as the model's view of the record
    (UI/UX audit 2026-10-18, UX-13)."""
    return _sample_lines(lines, max_chars, keep_edges, separator, gap_marker)[1]


def _sample_lines(
    lines: list[str], max_chars: int, keep_edges: int, separator: str, gap_marker: str,
) -> tuple[str, list[int]]:
    joined = separator.join(lines)
    if len(joined) <= max_chars:
        return joined, list(range(len(lines)))

    count = len(lines)
    edge = min(keep_edges, count // 2)
    head = lines[:edge]
    tail = lines[count - edge:] if edge else []
    middle = lines[edge:count - edge]
    middle_indices = list(range(edge, count - edge))

    fixed_parts = head + ([gap_marker] if middle else []) + tail
    budget = max_chars - len(separator.join(fixed_parts)) - len(separator) - len(gap_marker)
    selected_middle: list[str] = []
    selected_indices: list[int] = []
    if middle and budget > 0:
        low, high = 0, len(middle)
        while low <= high:
            selected_count = (low + high) // 2
            if selected_count:
                step = max(1, len(middle) // selected_count)
                candidate = middle[::step][:selected_count]
                candidate_indices = middle_indices[::step][:selected_count]
            else:
                candidate, candidate_indices = [], []
            if len(separator.join(candidate)) <= budget:
                selected_middle, selected_indices = candidate, candidate_indices
                low = selected_count + 1
            else:
                high = selected_count - 1

    head_idx: list[int | None] = list(range(edge))
    tail_idx: list[int | None] = list(range(count - edge, count)) if edge else []
    if selected_middle:
        parts = head + [gap_marker] + selected_middle + [gap_marker] + tail
        part_idx = head_idx + [None] + list(selected_indices) + [None] + tail_idx
    elif middle:
        parts = head + [gap_marker] + tail
        part_idx = head_idx + [None] + tail_idx
    else:
        parts = head + tail
        part_idx = head_idx + tail_idx
    # Only lines that survive the final cut whole count as shown: when the
    # two edges alone exceed max_chars the tail is truncated.
    kept: list[int] = []
    position = 0
    for part, index in zip(parts, part_idx):
        end = position + len(part)
        if index is not None and end <= max_chars:
            kept.append(index)
        position = end + len(separator)
    return separator.join(parts)[:max_chars], kept


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
