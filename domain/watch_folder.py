"""Pure "what's new" logic for a watched folder (B5b,
docs/IMPROVEMENT_PLAN_2026-08.ru.md).

Qt-free by design (see CLAUDE.md's domain/ layer rule) — core/watch_folder.py
owns the actual QFileSystemWatcher/QTimer debounce and calls into this
module only to decide which files a directory listing are genuinely new.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable, List, Set

# Bounded, not a full-file hash: a watch-folder drop can be a gigabyte
# recording, and this runs on every directory poll while a file is still
# stabilizing (see core/watch_folder.py) — a whole-file hash would make
# every poll as expensive as reading the entire file. Content_fingerprint()
# is deliberately NOT application.artifact_provenance.source_fingerprint:
# that one bakes the resolved path into its hash input (by design — see
# its own docstring), so it cannot recognize the exact scenario this
# module exists to catch, a copy of the same file dropped under a
# different name.
_SAMPLE_BYTES = 65536


def content_fingerprint(path: Path) -> str:
    """A cheap, path-independent identifier for a file's content: its size
    plus a hash of its first _SAMPLE_BYTES bytes. Two files with the same
    size and leading bytes are treated as the same file — good enough to
    catch an accidental duplicate drop, not a cryptographic guarantee."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            sample = fh.read(_SAMPLE_BYTES)
    except OSError:
        return f"unreadable:{path}"
    digest = hashlib.sha256(sample).hexdigest()[:16]
    return f"{size}:{digest}"


def new_files(seen: Set[str], listing: Iterable[Path]) -> List[Path]:
    """Entries in *listing* not already represented by a fingerprint in
    *seen*.

    *seen* holds ``content_fingerprint()`` values, not raw paths — a copy
    of the same file dropped into the watched folder under a different
    name must not be queued twice. This function only filters; it never
    mutates *seen* itself, since a candidate isn't "seen" until the
    caller has actually finished debouncing and queuing it (see
    core/watch_folder.py).
    """
    return [path for path in listing if content_fingerprint(path) not in seen]
