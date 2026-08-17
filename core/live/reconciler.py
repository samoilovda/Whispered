"""Stable-prefix, revision, and duplicate handling for one live source."""

from __future__ import annotations

import copy
import re
import string
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Iterable

from core.live.contracts import (
    LiveSegmentRevisions,
    SegmentUpdate,
    SpeechTurn,
)


class StablePrefixTracker:
    """Track text confirmed by consecutive partial decodes."""

    def __init__(self) -> None:
        self._last: dict[str, str] = {}
        self._stable: dict[str, str] = {}

    def observe(self, segment_id: str, text: str) -> str:
        """Return the longest exact word prefix seen in two revisions."""
        previous = self._last.get(segment_id)
        if previous is not None:
            common = _common_words(previous, text)
            if common:
                self._stable[segment_id] = common
        self._last[segment_id] = text
        return self._stable.get(segment_id, "")

    def stabilize(self, segment_id: str, text: str) -> str:
        """Observe text and hide a decode that would rewrite stable words."""
        stable = self.observe(segment_id, text)
        if not stable:
            return text
        stable_words = stable.split()
        current_words = text.split()
        if current_words[: len(stable_words)] == stable_words:
            return text
        return stable

    def commit(self, segment_id: str, text: str) -> str:
        """Commit final text and make it the stable prefix."""
        self._last[segment_id] = text
        self._stable[segment_id] = text
        return text

    def get(self, segment_id: str) -> str:
        return self._stable.get(segment_id, "")

    def clear(self, segment_id: str) -> None:
        self._last.pop(segment_id, None)
        self._stable.pop(segment_id, None)


@dataclass(frozen=True, slots=True)
class ReconcileStats:
    """Counters useful for live diagnostics."""

    partial_updates: int
    final_updates: int
    duplicate_segments: int


class LiveSegmentReconciler:
    """Reconcile partial/final segments for one source without rewriting text."""

    def __init__(self, source: str, dedup_overlap_seconds: float = 0.0) -> None:
        if not source:
            raise ValueError("source must not be empty")
        if dedup_overlap_seconds < 0:
            raise ValueError("dedup_overlap_seconds must be non-negative")
        self.source = source
        self.dedup_overlap_seconds = dedup_overlap_seconds
        self.revisions = LiveSegmentRevisions()
        self.stable_prefixes = StablePrefixTracker()
        self._final_segments: list[Any] = []
        self._finalized_ids: set[str] = set()
        self._partial_updates = 0
        self._final_updates = 0
        self._duplicate_segments = 0

    def accept(
        self,
        turn: SpeechTurn,
        segments: Iterable[Any],
        *,
        final: bool,
    ) -> tuple[SegmentUpdate, ...]:
        """Accept one decode and return externally visible revision updates."""
        if turn.source != self.source:
            raise ValueError(f"Reconciler is for {self.source!r}, got {turn.source!r}")

        updates: list[SegmentUpdate] = []
        for index, raw_segment in enumerate(segments):
            segment = _coerce_segment(raw_segment, self.source)
            segment_id = f"{self.source}:{turn.first_sequence}:{index}"
            latest = self.revisions.latest(segment_id)

            if segment_id in self._finalized_ids:
                continue
            if final and self._is_duplicate(segment):
                self._duplicate_segments += 1
                self._finalized_ids.add(segment_id)
                continue

            if final:
                update = (
                    self.revisions.finalize(segment_id, segment)
                    if latest is not None
                    else self.revisions.finalize_new(segment_id, segment)
                )
                self._finalized_ids.add(segment_id)
                self._final_segments.append(segment)
                self.stable_prefixes.commit(segment_id, segment.text)
                self._final_updates += 1
            elif latest is None:
                visible_text = self.stable_prefixes.stabilize(segment_id, segment.text)
                segment = _with_text(segment, visible_text)
                update = self.revisions.begin(segment_id, segment)
                self._partial_updates += 1
            else:
                visible_text = self.stable_prefixes.stabilize(segment_id, segment.text)
                segment = _with_text(segment, visible_text)
                update = self.revisions.revise(segment_id, segment)
                self._partial_updates += 1
            updates.append(update)
        return tuple(updates)

    def stable_prefix(self, segment_id: str) -> str:
        return self.stable_prefixes.get(segment_id)

    def stats(self) -> ReconcileStats:
        return ReconcileStats(
            partial_updates=self._partial_updates,
            final_updates=self._final_updates,
            duplicate_segments=self._duplicate_segments,
        )

    def _is_duplicate(self, segment: Any) -> bool:
        normalized = _normalize_text(segment.text)
        if not normalized:
            return False
        for previous in reversed(self._final_segments):
            if _normalize_text(previous.text) != normalized:
                continue
            overlap = min(segment.end, previous.end) - max(segment.start, previous.start)
            if overlap > self.dedup_overlap_seconds:
                return True
        return False


def _coerce_segment(raw_segment: Any, source: str) -> Any:
    if isinstance(raw_segment, dict):
        from domain.transcription import Segment

        return Segment(
            start=float(raw_segment["start"]),
            end=float(raw_segment["end"]),
            text=str(raw_segment["text"]),
            speaker=raw_segment.get("speaker", source),
            words=list(raw_segment.get("words", [])),
        )
    if not hasattr(raw_segment, "start"):
        raise TypeError("segment must provide start, end, text, speaker, and words")
    return raw_segment


def _common_words(previous: str, current: str) -> str:
    previous_words = previous.split()
    current_words = current.split()
    common: list[str] = []
    for old, new in zip(previous_words, current_words):
        if old != new:
            break
        common.append(old)
    return " ".join(common)


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    punctuation = string.punctuation + "…—–«»„“”"
    normalized = normalized.translate(str.maketrans("", "", punctuation))
    return re.sub(r"\s+", " ", normalized).strip()


def _with_text(segment: Any, text: str) -> Any:
    if segment.text == text:
        return segment
    try:
        return replace(segment, text=text)
    except TypeError:
        clone = copy.copy(segment)
        clone.text = text
        return clone
