"""Detection and parsing of Zoom's per-participant "separate audio" folders.

See ``docs/MULTITRACK_ZOOM_PLAN.ru.md`` (M1) for the format this was reverse
engineered from. Qt-free like the rest of ``domain/`` — enforced by
``tests/test_domain_multitrack.py`` alongside
``tests/test_domain_transcription.py``.

Duration is deliberately *not* computed here: probing a media file is IO,
which belongs in ``core/``. Callers fill it in afterwards via
:meth:`MultiTrackRecording.with_track_durations`.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Optional, Tuple

TRACKS_DIRNAME = "Audio Record"
CONF_FILENAME = "recording.conf"
_PREFIX = "audio"


@dataclass(frozen=True)
class TrackSpec:
    """One participant's own audio file inside ``Audio Record/``."""

    path: Path
    display_name: str
    participant_index: int
    duration: Optional[float] = None


@dataclass(frozen=True)
class MultiTrackRecording:
    """A parsed Zoom "record separate audio file for each participant" folder."""

    root: Path
    mixed_audio: Optional[Path]
    video: Optional[Path]
    tracks: Tuple[TrackSpec, ...]
    magic_number: str

    def with_track_durations(self, durations: Dict[Path, float]) -> "MultiTrackRecording":
        """Return a copy with each track's ``duration`` filled in from
        ``durations`` (keyed by :attr:`TrackSpec.path`). Tracks missing from
        the mapping keep their current duration."""
        new_tracks = tuple(
            replace(t, duration=durations.get(t.path, t.duration)) for t in self.tracks
        )
        return replace(self, tracks=new_tracks)


def parse_track_filename(filename: str, magic_number: str) -> Optional[Tuple[str, int]]:
    """Split a Zoom per-participant filename into ``(display_name, index)``.

    Observed scheme: ``audio`` + ``<display_name>`` + ``<index>`` +
    ``<magic_number>`` + extension, e.g. ``audioDen1857894770.m4a`` ->
    (``"Den"``, ``1``). Zoom truncates long display names and strips
    spaces, so the returned name is a hint, not a guaranteed identity.

    Returns ``None`` when the participant index can't be determined — most
    commonly a display name that itself ends in a digit with no index
    appended (e.g. a literal "User2" typed as a Zoom name), which is
    indistinguishable from "User" + index "2" by this scheme alone. Callers
    fall back to a positional name in that case.
    """
    stem = Path(filename).stem
    if not stem.startswith(_PREFIX):
        return None
    rest = stem[len(_PREFIX):]
    if magic_number and rest.endswith(magic_number):
        rest = rest[: -len(magic_number)]

    i = len(rest)
    while i > 0 and rest[i - 1].isdigit():
        i -= 1
    index_str = rest[i:]
    name = rest[:i]

    if not index_str:
        return None

    name = unicodedata.normalize("NFC", name).strip()
    return name, int(index_str)


def detect_multitrack(path: Path) -> Optional[MultiTrackRecording]:
    """Detect a Zoom multitrack recording folder from ``path``.

    ``path`` may be the recording folder itself, or any file inside it
    (including a file under ``Audio Record/``). Returns ``None`` when
    ``recording.conf`` or the ``Audio Record`` subfolder is missing — that's
    a normal single-file recording, not an error.
    """
    path = Path(path)
    candidates = [path if path.is_dir() else path.parent]
    if candidates[0].name == TRACKS_DIRNAME:
        candidates.append(candidates[0].parent)

    root = None
    for candidate in candidates:
        if (candidate / CONF_FILENAME).is_file() and (candidate / TRACKS_DIRNAME).is_dir():
            root = candidate
            break
    if root is None:
        return None

    try:
        conf = json.loads((root / CONF_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    magic_number = str(conf.get("magic_number", ""))
    items = conf.get("items") or []
    mixed_audio: Optional[Path] = None
    video: Optional[Path] = None
    if items:
        first = items[0]
        audio_name = first.get("audio")
        video_name = first.get("video")
        if audio_name and (root / audio_name).is_file():
            mixed_audio = root / audio_name
        if video_name and (root / video_name).is_file():
            video = root / video_name

    tracks_dir = root / TRACKS_DIRNAME
    track_files = sorted(
        (p for p in tracks_dir.iterdir() if p.is_file()),
        key=lambda p: p.name,
    )
    if not track_files:
        return None

    tracks = []
    fallback_index = 0
    for track_path in track_files:
        fallback_index += 1
        parsed = parse_track_filename(track_path.name, magic_number)
        if parsed is None:
            display_name, participant_index = f"Участник {fallback_index}", fallback_index
        else:
            display_name, participant_index = parsed
            if not display_name:
                display_name = f"Участник {participant_index}"
        tracks.append(
            TrackSpec(path=track_path, display_name=display_name, participant_index=participant_index)
        )

    return MultiTrackRecording(
        root=root,
        mixed_audio=mixed_audio,
        video=video,
        tracks=tuple(tracks),
        magic_number=magic_number,
    )
