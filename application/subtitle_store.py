"""Where a record's subtitle cues live (N02): ``subtitles.json`` in its
output folder — an editable layer of its own, never the transcript."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from application.user_edits import load_overlay, save_overlay
from domain.subtitles import SubtitleTrack


def track_file(artifact_dir: Path) -> Path:
    return artifact_dir / "subtitles.json"


def load_track(artifact_dir: Path) -> Optional[SubtitleTrack]:
    """The stored track; an empty one when there is none; ``None`` for a
    file made by a newer Whispered (left untouched)."""
    data = load_overlay(track_file(artifact_dir))
    return SubtitleTrack.from_dict(data) if data else SubtitleTrack()


def save_track(artifact_dir: Path, track: SubtitleTrack) -> None:
    save_overlay(track_file(artifact_dir), track.to_dict() if track.cues else {})


def write_subtitle_file(track: SubtitleTrack, path: str, fmt: str) -> None:
    """Write *track*'s cues as ``srt`` or ``vtt`` to *path*."""
    from domain import subtitles as s

    text = s.to_srt(track.cues) if fmt == "srt" else s.to_vtt(track.cues)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
