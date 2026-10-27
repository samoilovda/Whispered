"""The persistent recipe queue's shape (UI/UX audit 2026-10-18, UX-14 / T14).

A queue item is one source file plus an *immutable snapshot* of what to do
with it — the recipe (steps and params) and the transcription settings as
they were when the file was queued. Changing the global settings or the
selected recipe afterwards never changes an item already queued. The item's
state is stored, so a crash or restart loses nothing: an item that was
``running`` becomes ``interrupted`` and is offered for resuming — never
resumed silently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"

# Key in QueueSnapshot.llm for the plain (Default) AI settings frozen at queue time.
FROZEN_DEFAULT = "default_settings"

STATES = (PENDING, RUNNING, DONE, FAILED, CANCELLED, INTERRUPTED)
UNFINISHED = (PENDING, RUNNING, INTERRUPTED)      # still has work to do
RETRYABLE = (FAILED, CANCELLED, INTERRUPTED)      # can be put back to pending


@dataclass(frozen=True)
class QueueSnapshot:
    """What to do with a queued file, fixed at queue time."""

    recipe: dict[str, Any]          # domain.recipe.Recipe.to_dict()
    transcription: dict[str, Any]   # model, language, translate, n_threads, diarization, use_gpu
    # The local AI profile in force when it was queued (domain/ai_profile
    # LmProfile.to_dict()); empty = the plain settings. A later change of
    # profile never changes an item already waiting (N05). With the Default
    # profile it is {FROZEN_DEFAULT: {step params}} instead.
    llm: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"recipe": self.recipe, "transcription": self.transcription,
                           "llm": self.llm}, ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> Optional["QueueSnapshot"]:
        try:
            data = json.loads(raw)
            return cls(dict(data["recipe"]), dict(data["transcription"]),
                       dict(data.get("llm") or {}))
        except (TypeError, ValueError, KeyError):
            return None

    @property
    def steps(self) -> tuple[str, ...]:
        return tuple(self.recipe.get("steps", ()))

    @property
    def follow_up_steps(self) -> tuple[str, ...]:
        """The recipe's steps after transcription (the queue's transcriber
        produces the transcript itself)."""
        return tuple(s for s in self.steps if s != "transcribe")


@dataclass
class QueueItem:
    id: int
    batch_id: str
    position: int
    source_path: str
    snapshot: Optional[QueueSnapshot]
    state: str = PENDING
    stage: str = ""                 # what it is doing now: "transcribe" or a step name
    record_id: Optional[int] = None
    run_id: Optional[int] = None
    error: str = ""
    created_at: str = ""
    updated_at: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def unfinished(self) -> bool:
        return self.state in UNFINISHED

    @property
    def retryable(self) -> bool:
        return self.state in RETRYABLE
