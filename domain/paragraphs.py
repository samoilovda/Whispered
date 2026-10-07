"""Group transcript segments into readable paragraphs.

Whisper emits a segment per phrase or sentence; shown one per line, a
40-minute talk is three hundred short lines that read like subtitles,
not like text. A paragraph here is a run of segments that belong
together: same speaker, no long pause between them, and not so long that
it becomes a wall of text. Breaks prefer sentence ends.

Qt-free: the transcript view renders the result, export can reuse it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from domain.transcription import Segment

# A pause this long between two segments starts a new paragraph.
PAUSE_BREAK_S = 2.0
# Past either soft limit, the next sentence end closes the paragraph.
SOFT_MAX_S = 40.0
SOFT_MAX_CHARS = 420
# Past this, close the paragraph even mid-sentence: unpunctuated speech
# would otherwise run on for minutes.
HARD_MAX_CHARS = 900
# Never break on a pause before a paragraph has this much text — a short
# interjection ("Да.") followed by a pause stays with what follows.
MIN_CHARS_FOR_PAUSE_BREAK = 60

_SENTENCE_END = (".", "!", "?", "…", "»", '"', ")")


@dataclass
class Paragraph:
    """Consecutive segments shown as one paragraph.

    ``indices`` are positions in the original segment list, so callers can
    map a paragraph back to segments (seek, highlight, edit)."""

    indices: list[int] = field(default_factory=list)
    speaker: str | None = None
    start: float = 0.0
    end: float = 0.0
    chars: int = 0


def _ends_sentence(text: str) -> bool:
    return text.rstrip().endswith(_SENTENCE_END)


def group_paragraphs(segments: Sequence[Segment], by_speaker: bool = True) -> list[Paragraph]:
    """Split *segments* into paragraphs (see module docstring).

    *by_speaker*: a speaker change always starts a new paragraph. Turned
    off when speaker labels are hidden, so two speakers' lines can still
    flow into one paragraph by the other rules.
    """
    paragraphs: list[Paragraph] = []
    current: Paragraph | None = None
    previous: Segment | None = None
    for index, segment in enumerate(segments):
        text = segment.text.strip()
        if current is not None and previous is not None:
            gap = segment.start - previous.end
            duration = previous.end - current.start
            prev_text = previous.text.strip()
            speaker_changed = by_speaker and segment.speaker != current.speaker
            paused = gap >= PAUSE_BREAK_S and current.chars >= MIN_CHARS_FOR_PAUSE_BREAK
            long_enough = (
                (duration >= SOFT_MAX_S or current.chars >= SOFT_MAX_CHARS)
                and _ends_sentence(prev_text)
            )
            too_long = current.chars >= HARD_MAX_CHARS
            if speaker_changed or paused or long_enough or too_long:
                paragraphs.append(current)
                current = None
        if current is None:
            current = Paragraph(speaker=segment.speaker, start=segment.start)
        current.indices.append(index)
        current.end = segment.end
        current.chars += len(text) + (1 if current.chars else 0)
        previous = segment
    if current is not None:
        paragraphs.append(current)
    return paragraphs
