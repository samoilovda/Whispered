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
import uuid
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
    """Render *plan*. Both files are built next to their destinations and
    swapped in only when both exist, so a failure or cancel never costs the
    user a video or subtitles from an earlier render."""
    from video_cut import assemble_draft

    out = Path(plan.output)
    srt_path = Path(plan.srt_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:8]
    tmp_video = out.with_name(f".{out.stem}.{tag}.tmp.mp4")
    tmp_srt = srt_path.with_name(f".{srt_path.stem}.{tag}.tmp.srt")
    segments = [_Piece(a, b) for a, b in plan.pieces]
    try:
        if plan.subtitles:
            with open(tmp_srt, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(plan.subtitles)
        assemble_draft(plan.source, segments, str(tmp_video), on_progress=on_progress,
                       should_cancel=should_cancel)
        if plan.subtitles:
            os.replace(tmp_srt, srt_path)
        os.replace(tmp_video, out)
    finally:
        for leftover in (tmp_video, tmp_srt):
            try:
                leftover.unlink()
            except OSError:
                pass
