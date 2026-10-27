"""Pure "has a new recording finished?" logic for the recurring source
folder (e.g. the folder Zoom writes its recordings to).

Qt-free by design (see AGENTS.md's domain/ import rule) —
core/recording_watch.py owns the QFileSystemWatcher/QTimer and calls into
this module to decide when a new entry of the folder is complete.

Zoom converts a local recording only after the meeting ends: the meeting
sub-folder first holds ``double_click_to_convert_NN.zoom`` files, which
are replaced by the ``.m4a``/``.mp4`` files once conversion is done. An
entry counts as finished once it has usable media, no such in-progress
file, and its listing (names, sizes, modification times) has stayed the
same for the settle window.
"""

from __future__ import annotations

from pathlib import Path
from typing import Container, Dict, List, Mapping, Optional, Set, Tuple

from domain.latest_source import pick_media

# Files that mean "still being converted/written": Zoom's unconverted
# recording plus the usual temporary/partial-download suffixes.
_IN_PROGRESS_SUFFIXES = {".zoom", ".tmp", ".part", ".download", ".crdownload"}

Snapshot = Tuple[Tuple[str, int, int], ...]


def default_recording_folder() -> str:
    """Where Zoom saves local recordings by default (macOS and Windows
    alike) — used when ``Config.source_folder`` is unset."""
    return str(Path.home() / "Documents" / "Zoom")


def _file_state(path: Path) -> Optional[Tuple[str, int, int]]:
    try:
        st = path.stat()
    except OSError:
        return None
    return (path.name, st.st_size, st.st_mtime_ns)


def entry_snapshot(entry: Path, extensions: Container[str]) -> Optional[Snapshot]:
    """A fingerprint of *entry* (a loose media file or a recording
    sub-folder) when it looks finished, ``None`` while it is still being
    written or holds nothing to transcribe."""
    if entry.is_file():
        if entry.suffix.lower() not in extensions:
            return None
        state = _file_state(entry)
        if state is None or state[1] == 0:
            return None
        return (state,)
    if not entry.is_dir():
        return None
    try:
        files = [p for p in entry.iterdir() if not p.name.startswith(".") and p.is_file()]
    except OSError:
        return None
    if any(p.suffix.lower() in _IN_PROGRESS_SUFFIXES for p in files):
        return None
    if pick_media(entry, extensions) is None:
        return None
    states = []
    for path in files:
        state = _file_state(path)
        if state is None:
            return None
        if path.suffix.lower() in extensions and state[1] == 0:
            return None  # a media file created but not written yet
        states.append(state)
    return tuple(sorted(states))


def scan(root: str | Path, extensions: Container[str]) -> Dict[str, Optional[Snapshot]]:
    """Every visible entry of *root* mapped to its ``entry_snapshot()``;
    empty when *root* is missing or unreadable."""
    base = Path(root)
    try:
        entries = [p for p in base.iterdir() if not p.name.startswith(".")]
    except OSError:
        return {}
    return {str(p): entry_snapshot(p, extensions) for p in entries}


def media_for(entry: str | Path, extensions: Container[str]) -> Optional[Path]:
    """The file to transcribe from a finished entry: the file itself, or
    the best file of a recording sub-folder (``pick_media``)."""
    path = Path(entry)
    if path.is_file():
        return path
    return pick_media(path, extensions)


class SettleTracker:
    """Remembers which entries were already there and reports each new one
    once, after its snapshot has stayed unchanged for *settle_seconds*.

    Entries that are finished when ``baseline()`` runs are never reported
    — they are old recordings. An entry still converting at that moment
    is not part of the baseline, so a meeting that is being converted
    when the app starts is still offered once it is done.
    """

    def __init__(self, settle_seconds: float) -> None:
        self._settle = settle_seconds
        self._known: Set[str] = set()
        self._pending: Dict[str, Tuple[Snapshot, float]] = {}

    def baseline(self, snapshots: Mapping[str, Optional[Snapshot]]) -> None:
        self._known = {key for key, snap in snapshots.items() if snap is not None}
        self._pending.clear()

    def observe(self, snapshots: Mapping[str, Optional[Snapshot]], now: float) -> List[str]:
        """Feed a fresh ``scan()``; returns the entries that just settled,
        each reported only once."""
        settled: List[str] = []
        for key, snap in snapshots.items():
            if key in self._known:
                continue
            if snap is None:
                self._pending.pop(key, None)
                continue
            previous = self._pending.get(key)
            if previous is None or previous[0] != snap:
                self._pending[key] = (snap, now)
            elif now - previous[1] >= self._settle:
                del self._pending[key]
                self._known.add(key)
                settled.append(key)
        for gone in set(self._pending) - set(snapshots):
            del self._pending[gone]
        return sorted(settled)

    def is_settling(self) -> bool:
        """True while some finished-looking entry still waits out the
        settle window — the caller keeps polling until it is reported."""
        return bool(self._pending)

    def unfinished(self, snapshots: Mapping[str, Optional[Snapshot]]) -> List[str]:
        """Entries that are neither reported nor known yet — the
        sub-folders worth watching for the conversion to finish."""
        return sorted(key for key in snapshots if key not in self._known)
