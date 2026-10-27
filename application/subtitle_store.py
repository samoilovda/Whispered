"""Where a record's subtitle cues live (N02): ``subtitles.json`` in its
output folder — an editable layer of its own, never the transcript."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from application.user_edits import load_overlay, save_overlay
from domain.subtitles import SubtitleCue, SubtitleTrack


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


MAX_IMPORT_BYTES = 5 * 1024 * 1024


class SubtitleImportError(ValueError):
    """The file cannot be read as a subtitle track (message says why)."""


def read_subtitle_file(path: str) -> list[SubtitleCue]:
    """The cues of an SRT or WebVTT file, ordered by start time. Raises
    :class:`SubtitleImportError` for an unreadable, oversized, non-text or
    cue-less file."""
    from domain import subtitles as s

    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_IMPORT_BYTES + 1)
    except OSError as exc:
        raise SubtitleImportError(str(exc)) from exc
    if len(raw) > MAX_IMPORT_BYTES:
        raise SubtitleImportError("too_large")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SubtitleImportError("not_utf8") from exc
    cues = [c for c in s.parse_cues(text) if c.end > c.start and c.text.strip()]
    if not cues:
        raise SubtitleImportError("no_cues")
    return sorted(cues, key=lambda cue: (cue.start, cue.end))


def combine_cues(existing: list[SubtitleCue], imported: list[SubtitleCue], mode: str) -> list[SubtitleCue]:
    """``replace`` → the imported cues; ``add`` → both, in time order."""
    if mode == "replace":
        return list(imported)
    return sorted([*existing, *imported], key=lambda cue: (cue.start, cue.end))
