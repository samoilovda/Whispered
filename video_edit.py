"""Whispered - Video Edit: algorithmic and LLM-assisted segment cleanup.

mark_pauses() — pure algorithm, no dependencies.
LLMFillerWorker — optional LLM pass via existing LMStudioClient infrastructure.
"""

from __future__ import annotations

import re
from typing import Optional

from core.logger import get_logger

logger = get_logger(__name__)

# Filler words / phrases (lower-case, stripped of punctuation) that mark a
# segment as likely unwanted when it contains only these tokens.
_FILLER_WORDS: frozenset[str] = frozenset({
    # English
    "um", "uh", "uhm", "er", "ah", "hmm", "hm", "mhm", "mm",
    "like", "right", "okay", "ok", "so", "well", "actually",
    # Russian
    "эм", "ну", "вот", "это", "значит", "типа", "короче",
    "ладно", "хорошо", "понятно", "собственно", "буквально",
})


def mark_pauses(
    segments,
    min_duration: float = 0.5,
    gap_threshold: float = 0.0,
) -> list[int]:
    """Return indices of segments that are likely pauses or filler-only.

    A segment is marked when ANY of:
    - its duration < min_duration seconds, OR
    - its text (after stripping punctuation) consists entirely of filler words.

    Optionally, if gap_threshold > 0, also mark segments that are preceded by
    a silence gap longer than gap_threshold (the gap itself is silence, but the
    segment immediately after a long pause is often a fresh thought — callers
    may prefer to use this to mark the *preceding* segment instead, depending
    on workflow).

    Parameters
    ----------
    segments      : Sequence of objects with .start, .end, .text attributes.
    min_duration  : Segments shorter than this (seconds) are marked.
    gap_threshold : If > 0, mark segments where (seg.start - prev.end) >
                    gap_threshold. Disabled by default (0.0 = off).
    """
    indices: list[int] = []
    prev_end: float = 0.0

    for i, seg in enumerate(segments):
        duration = seg.end - seg.start
        words = re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", seg.text.lower())

        is_short = duration < min_duration
        is_filler = bool(words) and all(w in _FILLER_WORDS for w in words)
        is_after_long_gap = (
            gap_threshold > 0
            and i > 0
            and (seg.start - prev_end) > gap_threshold
        )

        if is_short or is_filler or is_after_long_gap:
            indices.append(i)

        prev_end = seg.end

    return indices


# ---------------------------------------------------------------------------
# LLM-assisted filler detection (optional, requires LM Studio running)
# ---------------------------------------------------------------------------

from PyQt6.QtCore import pyqtSignal
from core.base_worker import BaseWorker
from core.prompts import load_prompt


_FILLER_PROMPT_FALLBACK = """\
You are a video editor assistant. Below is a transcript with timestamps.
Identify segments that should be CUT: repeated words, false starts, "um/uh/эм/ну",
long pauses, off-topic tangents, or botched takes.

Return ONLY a JSON array of start times (in seconds, as numbers) for segments to cut.
Example: [0.0, 12.5, 47.3]

If nothing should be cut, return: []

Transcript:
"""


class LLMFillerWorker(BaseWorker):
    """Ask the LLM to identify filler/pause segments by start time.

    Emits finished(list[float]) — start times (seconds) of segments to cut.
    The caller should match these to the nearest segment index.
    """

    finished = pyqtSignal(list)       # list[float] of start times to cut
    error_occurred = pyqtSignal(str)

    def __init__(self, segments, lm_url: str, parent=None):
        super().__init__(parent)
        self._segments = segments
        self._lm_url = lm_url

    def _on_error(self, msg: str) -> None:
        self.error_occurred.emit(msg)

    def _execute(self):
        import json
        import re as _re

        from core.lm_client import LMStudioClient
        from core.llm_text import fit_to_context

        client = LMStudioClient(self._lm_url)
        system_prompt = load_prompt("fillers", fallback=_FILLER_PROMPT_FALLBACK)

        lines = []
        for seg in self._segments:
            start_s = int(seg.start) if hasattr(seg, "start") else 0
            text = (seg.text if hasattr(seg, "text") else "").strip()
            lines.append(f"[{start_s}s] {text}")
        transcript = fit_to_context("\n".join(lines), 32_000)

        messages = [{"role": "user", "content": system_prompt + "\n" + transcript}]
        raw = client.chat_completion_stream(
            messages=messages,
            is_cancelled=self._cancelled.is_set,
            temperature=0.1,
        )

        if self._cancelled.is_set():
            self.finished.emit([])
            return
        if raw is None:
            self.error_occurred.emit(f"LM Studio did not respond ({self._lm_url}).")
            return

        # Parse JSON array of numbers
        try:
            cleaned = raw.strip()
            cleaned = _re.sub(r'^```[a-z]*\n?', '', cleaned, flags=_re.MULTILINE)
            cleaned = _re.sub(r'\n?```$', '', cleaned, flags=_re.MULTILINE)
            result = json.loads(cleaned)
            if isinstance(result, list):
                times = [float(x) for x in result if isinstance(x, (int, float))]
                self.finished.emit(times)
                return
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: extract first JSON array
        match = _re.search(r'\[[\d.,\s]*\]', raw)
        if match:
            try:
                times = [float(x) for x in json.loads(match.group())]
                self.finished.emit(times)
                return
            except (json.JSONDecodeError, ValueError):
                pass

        logger.warning("LLMFillerWorker: could not parse response: %.80s", raw)
        self.finished.emit([])


def times_to_indices(start_times: list[float], segments, tolerance: float = 1.0) -> list[int]:
    """Map a list of start times (from LLM) to segment indices.

    For each time, picks the segment whose .start is within `tolerance` seconds.
    """
    indices = []
    for t in start_times:
        best_idx = None
        best_dist = float("inf")
        for i, seg in enumerate(segments):
            d = abs(seg.start - t)
            if d < best_dist and d <= tolerance:
                best_dist = d
                best_idx = i
        if best_idx is not None and best_idx not in indices:
            indices.append(best_idx)
    return sorted(indices)
