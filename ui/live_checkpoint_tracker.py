"""
Whispered UI - Live checkpoint tracker
Accumulates finalized live-transcription segments and checkpoints them to
history as they arrive, so a forced quit or crash during a long live
session loses at most the last unfinalized segment rather than the
whole thing.

No PyQt dependency of its own: HistoryStore (core/history.py) is plain
SQLite. The one widget touch the pre-extraction code made directly —
refreshing the Library view after a checkpoint — is left to the caller
(see MainWindow._checkpoint_live_history), the same split
PresetChainController uses between owned state and caller-driven widgets.
"""

from __future__ import annotations

from typing import Any, Optional

from core.history import HistoryStore
from core.i18n import tr
from transcriber import TranscriptionResult


class LiveCheckpointTracker:
    """Tracks one live session's finalized segments and its history row.

    Attributes
    ----------
    history_record_id
        The history row this session is checkpointing into, or ``None``
        before the first segment finalizes.
    source_name, model_name
        Captured once at ``start()`` — read back by MainWindow for the
        eventual final save and for display (e.g. the YouTube tab's
        source-name field) while this session is the active one.
    """

    def __init__(self) -> None:
        self.history_record_id: Optional[int] = None
        self.source_name: str = ""
        self.model_name: str = ""
        self._final_segments: dict[str, Any] = {}

    def start(self, source_name: str, model_name: str) -> None:
        """Begin tracking a fresh live session."""
        self.history_record_id = None
        self.source_name = source_name
        self.model_name = model_name
        self._final_segments = {}

    def accept_final(self, segment_id: str, segment: Any) -> None:
        """Record one segment that has reached SegmentState.FINAL."""
        self._final_segments[segment_id] = segment

    def build_result(self, language: str) -> TranscriptionResult:
        """Assemble everything finalized so far into a TranscriptionResult."""
        segments = sorted(
            self._final_segments.values(),
            key=lambda segment: (float(segment.start), float(segment.end)),
        )
        duration = max((float(segment.end) for segment in segments), default=0.0)
        return TranscriptionResult(
            segments=segments,
            language=language,
            duration=duration,
            speaker_names={
                "mic": tr("live_source_mic"),
                "system": tr("live_source_system"),
            },
        )

    def checkpoint(self, store: HistoryStore, language: str) -> bool:
        """Persist whatever has finalized so far.

        Returns True if a row was actually written (there was something to
        save) — the caller should refresh the Library view only then.
        """
        result = self.build_result(language)
        if not result.segments:
            return False
        if self.history_record_id is None:
            self.history_record_id = store.add(
                result,
                source_path="",
                source_name=self.source_name,
                model=self.model_name,
                speaker_names=result.speaker_names,
                source_kind="live",
            )
        else:
            store.update_result(
                self.history_record_id,
                result,
                speaker_names=result.speaker_names,
            )
        return True
