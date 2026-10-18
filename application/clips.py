"""Storing and rendering a record's short clips (N07).

Qt-free. The record's clips live in ``clips.json`` in its output folder.
Rendering cuts the source with ffmpeg (``video_cut.assemble_draft``:
frame-accurate, to a temporary file that replaces the target only when
complete) and then writes the clip's subtitles beside it as ``.srt``; the
source file is only ever read, and a cancelled or failed render leaves
neither video nor subtitles behind.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from application.user_edits import load_overlay, save_overlay
from domain import clips as c
from domain import subtitles as s


def clips_file(artifact_dir: Path) -> Path:
    return artifact_dir / "clips.json"


def load_clips(artifact_dir: Path) -> Optional[c.ClipSet]:
    """The record's clips; an empty set when none; ``None`` for a newer schema."""
    data = load_overlay(clips_file(artifact_dir))
    return c.ClipSet.from_dict(data) if data else c.ClipSet()


def save_clips(artifact_dir: Path, clips: c.ClipSet) -> None:
    save_overlay(clips_file(artifact_dir), clips.to_dict() if clips.clips else {})


@dataclass
class _Piece:
    start: float
    end: float


@dataclass
class RenderPlan:
    source: str
    output: str
    pieces: list[tuple[float, float]]
    subtitles: str            # SRT text, "" when there is nothing to show
    srt_path: str


def plan_render(
    clip: c.ClipDraft, *, source: str, output: str, kept: Sequence[Any],
    cues: Sequence[s.SubtitleCue], media_duration: Optional[float],
) -> RenderPlan:
    """Check *clip* and work out exactly what rendering it would do.
    Raises ``ValueError`` with the reason when it cannot be rendered."""
    problems = c.check_clip(clip, media_duration=media_duration,
                            edited_duration=c.edited_duration(kept) if kept else None)
    if problems:
        raise ValueError(problems[0])
    if not source or not os.path.isfile(source):
        raise ValueError("source_missing")
    out = Path(output)
    if out.suffix.lower() != ".mp4":
        raise ValueError("not_mp4")
    if out.resolve() == Path(source).resolve() or out.with_suffix(".srt").resolve() == Path(source).resolve():
        raise ValueError("same_as_source")
    pieces = c.source_pieces(clip, kept)
    if not pieces:
        raise ValueError(c.CLIP_NO_PIECES)
    cut = c.cues_for_clip(cues, pieces)
    return RenderPlan(source, output, pieces, s.to_srt(cut) if cut else "", str(out.with_suffix(".srt")))


def render(
    plan: RenderPlan, *, on_progress: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> None:
    """Render *plan*. The subtitles are written only after the video is in
    place, and removed again if that write fails."""
    from video_cut import assemble_draft

    segments = [_Piece(a, b) for a, b in plan.pieces]
    assemble_draft(plan.source, segments, plan.output, on_progress=on_progress,
                   should_cancel=should_cancel)
    if plan.subtitles:
        try:
            with open(plan.srt_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(plan.subtitles)
        except OSError:
            try:
                os.remove(plan.output)
            except OSError:
                pass
            raise
