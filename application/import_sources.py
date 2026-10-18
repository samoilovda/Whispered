"""What a drop (or any multi-source pick) actually imports.

UI/UX audit 2026-10-18, UX-03 / T03: the start screen's drop zone used to
take only the first dropped URL while the window behind it took them all,
so the same two files produced one result over the drop zone and two over
the rest of the window. Every drop target now hands its local paths to
``collect_import_sources()`` and lets the window decide "one file → start
screen, several → queue".

Qt-free: callers convert ``QUrl``s to local paths first (counting the
non-local ones), so the rules here are testable without a GUI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ImportBatch:
    """The outcome of normalizing dropped sources, in drop order."""

    files: list[str] = field(default_factory=list)
    unsupported: int = 0     # local files with an extension we can't read
    duplicates: int = 0      # the same file twice (directly or via a folder)
    unreadable: list[str] = field(default_factory=list)  # folders we couldn't list
    non_local: int = 0       # URLs that aren't local files (http://…)

    @property
    def skipped(self) -> int:
        return self.unsupported + self.duplicates + len(self.unreadable) + self.non_local


def _identity(path: str) -> str:
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:
        return os.path.normcase(os.path.abspath(path))


def collect_import_sources(
    paths: Iterable[str],
    is_supported: Callable[[str], bool],
    *,
    non_local: int = 0,
) -> ImportBatch:
    """Reduce dropped *paths* (files and folders) to supported files.

    A folder expands one level deep — its own files, sorted by name, not
    its sub-folders: a folder full of unrelated sub-folders must not
    silently pull in hundreds of files. A folder that can't be listed is
    reported in ``unreadable`` without affecting the other sources. The same file reached twice is kept once.
    """
    batch = ImportBatch(non_local=non_local)
    seen: set[str] = set()

    def add(path: str) -> None:
        key = _identity(path)
        if key in seen:
            batch.duplicates += 1
            return
        seen.add(key)
        batch.files.append(path)

    for path in paths:
        if os.path.isdir(path):
            try:
                entries = sorted(Path(path).iterdir())
            except OSError as exc:
                logger.info("Dropped folder %s is unreadable: %s", path, exc)
                batch.unreadable.append(path)
                continue
            for entry in entries:
                if not entry.is_file():
                    continue
                if is_supported(str(entry)):
                    add(str(entry))
                else:
                    batch.unsupported += 1
        elif is_supported(path):
            add(path)
        else:
            batch.unsupported += 1
    return batch


def is_droppable(path: str, is_supported: Callable[[str], bool]) -> bool:
    """Whether a drag carrying *path* should be accepted at all."""
    return is_supported(path) or os.path.isdir(path)
