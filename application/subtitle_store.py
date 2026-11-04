"""Where a record's subtitle cues live (N02): ``subtitles.json`` in its
output folder — an editable layer of its own, never the transcript."""

from __future__ import annotations

import re

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


_CYRILLIC = re.compile("[\u0400-\u04ff]")
_STRAY_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def decode_subtitle_bytes(raw: bytes) -> tuple[str, str]:
    """(text, encoding) of a subtitle file: UTF-8 (with or without BOM),
    UTF-16 by its BOM, else Windows-1251 — what Russian subtitles from
    Windows tools usually are (audit 2026-11-04 W20). cp1251 decodes almost
    any bytes, so it is accepted only for text whose letters are mostly
    Cyrillic, with no stray control characters. Raises :class:`SubtitleImportError`
    ("not_utf8") when none of them fits."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError as exc:
            raise SubtitleImportError("not_utf8") from exc
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        text = raw.decode("cp1251")
    except UnicodeDecodeError as exc:
        raise SubtitleImportError("not_utf8") from exc
    letters = [ch for ch in text if ch.isalpha()]
    cyrillic = sum(1 for ch in letters if _CYRILLIC.match(ch))
    # Mostly Cyrillic letters: a Latin-1 "café" decodes as "cafй" too.
    if letters and cyrillic * 2 > len(letters) and not _STRAY_CONTROL.search(text):
        return text, "cp1251"
    raise SubtitleImportError("not_utf8")


def read_subtitle_file(path: str) -> list[SubtitleCue]:
    """The cues of an SRT or WebVTT file, ordered by start time. Raises
    :class:`SubtitleImportError` for an unreadable, oversized, non-text or
    cue-less file."""
    return read_subtitle_file_detailed(path)[0]


def read_subtitle_file_detailed(path: str) -> tuple[list[SubtitleCue], str]:
    """:func:`read_subtitle_file` plus the encoding the text was read in."""
    from domain import subtitles as s

    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_IMPORT_BYTES + 1)
    except OSError as exc:
        raise SubtitleImportError(str(exc)) from exc
    if len(raw) > MAX_IMPORT_BYTES:
        raise SubtitleImportError("too_large")
    text, encoding = decode_subtitle_bytes(raw)
    cues = [c for c in s.parse_cues(text) if c.end > c.start and c.text.strip()]
    if not cues:
        raise SubtitleImportError("no_cues")
    return sorted(cues, key=lambda cue: (cue.start, cue.end)), encoding


def combine_cues(existing: list[SubtitleCue], imported: list[SubtitleCue], mode: str) -> list[SubtitleCue]:
    """``replace`` → the imported cues; ``add`` → both, in time order."""
    if mode == "replace":
        return list(imported)
    return sorted([*existing, *imported], key=lambda cue: (cue.start, cue.end))
