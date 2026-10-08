"""Pure "newest source" logic for the recurring-source folder (e.g. the
folder Zoom writes its recordings to).

Qt-free by design (see CLAUDE.md's domain/ layer rule). A source folder
holds either loose media files or one sub-folder per recording (Zoom's
layout: ``2026-10-08 10.00.00 Meeting/`` with ``audio_only.m4a``, a video
and per-speaker tracks). ``find_latest_media()`` returns the single file
to transcribe from the most recently modified entry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Container, Iterator, Optional


def _is_media(path: Path, extensions: Container[str]) -> bool:
    return path.suffix.lower() in extensions


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _media_in(folder: Path, extensions: Container[str]) -> list[Path]:
    try:
        return [
            p for p in folder.iterdir()
            if not p.name.startswith(".") and p.is_file() and _is_media(p, extensions)
        ]
    except OSError:
        return []


def pick_media(folder: Path, extensions: Container[str]) -> Optional[Path]:
    """The best single file of one recording folder: an ``audio_only``
    track first (Zoom's mixed audio), then any audio over video, then the
    largest file — per-speaker tracks and the main video are bigger than
    a mix, but an audio-only mix is what transcription wants."""
    files = _media_in(folder, extensions)
    if not files:
        return None

    audio = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".wma", ".aac"}

    def rank(path: Path) -> tuple[bool, bool, int]:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return (path.stem.lower().startswith("audio_only"), path.suffix.lower() in audio, size)

    return max(files, key=rank)


def _entries_newest_first(root: Path, extensions: Container[str]) -> Iterator[Path]:
    try:
        entries = [p for p in root.iterdir() if not p.name.startswith(".")]
    except OSError:
        return

    def freshness(path: Path) -> float:
        if path.is_dir():
            return max([_mtime(path)] + [_mtime(f) for f in _media_in(path, extensions)])
        return _mtime(path)

    candidates = [
        p for p in entries
        if p.is_dir() or (p.is_file() and _is_media(p, extensions))
    ]
    yield from sorted(candidates, key=freshness, reverse=True)


def find_latest_media(root: str | Path, extensions: Container[str]) -> Optional[Path]:
    """The media file to use from the most recently modified entry of
    *root* (file or sub-folder, by last-modified time); ``None`` when
    *root* is missing or holds nothing usable. A sub-folder without any
    media is skipped in favour of the next newest entry."""
    base = Path(root)
    if not base.is_dir():
        return None
    for entry in _entries_newest_first(base, extensions):
        if entry.is_file():
            return entry
        picked = pick_media(entry, extensions)
        if picked is not None:
            return picked
    return None


def find_companion_video(
    source: str | Path, video_extensions: Container[str]
) -> Optional[Path]:
    """The video recorded together with an audio *source*, if any: a video
    next to it with the same name, or — for a Zoom-style ``audio_only``
    mix — the largest video in the same recording folder. Any other audio
    file is left alone: an unrelated video in the same folder is not its
    picture."""
    path = Path(source)
    folder = path.parent
    try:
        videos = [
            p for p in folder.iterdir()
            if p.is_file() and not p.name.startswith(".")
            and p.suffix.lower() in video_extensions
        ]
    except OSError:
        return None
    same_name = [p for p in videos if p.stem == path.stem]
    if same_name:
        return same_name[0]
    if path.stem.lower().startswith("audio_only") and videos:
        return max(videos, key=lambda p: p.stat().st_size)
    return None
