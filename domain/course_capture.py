"""Queue of system-audio-capture items for the Course Capture panel.

One item = one lesson: the user starts capture, plays the lesson in a
browser (or any app) they're legitimately watching, stops capture, and the
accumulated segments become a normal ``TranscriptionResult`` — same DTO the
batch/file pipeline produces, so downstream export/history code does not
need to know the transcript came from a live capture instead of a file.
Qt-free like the rest of ``domain/`` — enforced by
``tests/test_domain_course_capture.py`` alongside
``tests/test_domain_transcription.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from domain.transcription import Segment, TranscriptionResult


class CaptureItemStatus(str, Enum):
    PENDING = "pending"
    RECORDING = "recording"
    DONE = "done"
    ERROR = "error"


@dataclass
class CaptureQueueItem:
    """One lesson slot in the capture queue."""

    id: str
    title: str
    status: CaptureItemStatus = CaptureItemStatus.PENDING
    result: Optional[TranscriptionResult] = None
    error: str = ""

    @property
    def is_complete(self) -> bool:
        return self.status in (CaptureItemStatus.DONE, CaptureItemStatus.ERROR)


@dataclass
class CaptureQueue:
    """Ordered list of :class:`CaptureQueueItem` plus the transitions the
    Course Capture panel drives them through.

    At most one item may be ``RECORDING`` at a time — that's the panel's
    single active capture session.
    """

    items: List[CaptureQueueItem] = field(default_factory=list)

    def add_item(self, title: str) -> CaptureQueueItem:
        item = CaptureQueueItem(id=uuid.uuid4().hex, title=title)
        self.items.append(item)
        return item

    def remove_item(self, item_id: str) -> None:
        self.items = [item for item in self.items if item.id != item_id]

    def move_item(self, item_id: str, new_index: int) -> None:
        index = self._index_of(item_id)
        item = self.items.pop(index)
        new_index = max(0, min(new_index, len(self.items)))
        self.items.insert(new_index, item)

    def item_by_id(self, item_id: str) -> CaptureQueueItem:
        index = self._index_of(item_id)
        return self.items[index]

    def next_pending(self) -> Optional[CaptureQueueItem]:
        for item in self.items:
            if item.status is CaptureItemStatus.PENDING:
                return item
        return None

    def start_recording(self, item_id: str) -> CaptureQueueItem:
        if any(item.status is CaptureItemStatus.RECORDING for item in self.items):
            raise ValueError("another item is already recording")
        item = self.item_by_id(item_id)
        item.status = CaptureItemStatus.RECORDING
        item.error = ""
        return item

    def finish_recording(self, item_id: str, result: TranscriptionResult) -> CaptureQueueItem:
        item = self.item_by_id(item_id)
        item.status = CaptureItemStatus.DONE
        item.result = result
        return item

    def fail_recording(self, item_id: str, error: str) -> CaptureQueueItem:
        item = self.item_by_id(item_id)
        item.status = CaptureItemStatus.ERROR
        item.error = error
        return item

    def combined_result(self, heading_prefix: str = "## ") -> Optional[TranscriptionResult]:
        """One synthetic ``TranscriptionResult`` stitching every ``DONE``
        item's segments together, in queue order, each preceded by a
        heading segment carrying the lesson title.

        Segments are re-timed onto one monotonic timeline (a heading
        segment plus every content segment shifted by the running offset)
        so the result stays a valid transcript for any exporter, even
        though the primary use is a plain-text export that only reads
        ``full_text`` and ignores timing. Returns ``None`` when no item is
        done yet.
        """
        done_items = [
            item for item in self.items
            if item.status is CaptureItemStatus.DONE and item.result is not None
        ]
        if not done_items:
            return None
        segments: List[Segment] = []
        offset = 0.0
        language = ""
        for item in done_items:
            result = item.result
            assert result is not None
            language = language or result.language
            heading_end = offset + 1.0
            segments.append(Segment(start=offset, end=heading_end, text=f"{heading_prefix}{item.title}"))
            offset = heading_end
            for seg in result.segments:
                segments.append(Segment(
                    start=offset + seg.start,
                    end=offset + seg.end,
                    text=seg.text,
                    speaker=seg.speaker,
                    words=list(seg.words),
                ))
            offset += result.duration
        return TranscriptionResult(segments=segments, language=language, duration=offset)

    def _index_of(self, item_id: str) -> int:
        for index, item in enumerate(self.items):
            if item.id == item_id:
                return index
        raise KeyError(f"no capture queue item with id {item_id!r}")
