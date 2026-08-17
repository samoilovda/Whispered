"""Transcription result types.

Extracted from ``transcriber.py`` (see docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md,
R7-pre): these dataclasses carry no Qt or IO dependency of their own, but
living in ``transcriber.py`` — which imports ``PyQt6.QtCore`` for the
``Transcriber`` worker — meant anything that only needed the DTOs (exporters,
the Live subsystem) transitively pulled in Qt just by importing them.
``transcriber.py`` re-exports these names for backward compatibility, so
existing ``from transcriber import TranscriptionResult`` call sites are
unaffected; new code should import from here directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Word:
    """A single word with its timing from word-level transcription."""
    start: float
    end: float
    text: str


@dataclass
class Segment:
    """Represents a transcription segment with timing."""
    start: float  # Start time in seconds
    end: float    # End time in seconds
    text: str     # Transcribed text
    speaker: Optional[str] = None  # Speaker label (e.g., "Speaker 1")
    words: List['Word'] = field(default_factory=list)  # Word-level timings (video mode only)


@dataclass
class TranscriptionResult:
    """Complete transcription result."""
    segments: List[Segment]
    language: str
    duration: float
    # Maps raw speaker id (e.g. "Speaker 1") to a user-assigned display name.
    # Empty by default; populated when the user renames speakers.
    speaker_names: Dict[str, str] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        """Get the complete transcription as plain text."""
        return ' '.join(seg.text.strip() for seg in self.segments)

    def speaker_label(self, speaker_id: Optional[str]) -> Optional[str]:
        """Resolve a speaker id to its display name (or the id if unmapped)."""
        if not speaker_id:
            return None
        return self.speaker_names.get(speaker_id, speaker_id)
