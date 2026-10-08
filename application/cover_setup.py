"""A record's cover setup, kept so reopening the record redraws the same cover.

Qt-free. The Cover workspace (``ui/cover_view.py``) holds the layout,
variant choice, "Shuffle" count, title/names and the chosen photos with
their framing only in memory; the publish wizard's draft
(``application/youtube_publish.py``) keeps texts, not these. Without them a
restart re-approves the cover with another "auto" palette and without the
guest's photo. They live in ``<artifact_dir>/cover.setup.json``.

Photos picked from disk or pulled from the video (scratch files that vanish
with the app) are copied into ``<artifact_dir>/cover_photos/`` under a
content-hashed name, so a changed photo never reuses the cover step's
cached render. The host photo from Settings is stored as "the host photo"
rather than copied: it stays the fallback for ``photo_a``.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from application.user_edits import load_overlay, save_overlay
from core.logger import get_logger

logger = get_logger(__name__)

SETUP_FILE = "cover.setup.json"
PHOTO_DIR = "cover_photos"
PHOTO_SLOTS = ("photo_a", "photo_b")
HOST_PHOTO = "host"


@dataclass(frozen=True)
class PhotoSetup:
    """One photo slot. ``path`` is ``None`` for the host photo from Settings."""

    path: str | None
    focus: tuple[float, float] = (0.5, 0.5)
    zoom: float = 1.0


@dataclass(frozen=True)
class CoverSetup:
    layout: str
    variant: str
    shuffle: int = 0
    title: str = ""
    names: str = ""
    photos: dict[str, PhotoSetup] = field(default_factory=dict)


def setup_path(art_dir: Path) -> Path:
    return art_dir / SETUP_FILE


def _is_inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def store_photo(art_dir: Path, slot: str, source: str) -> str:
    """Copy *source* into the record's photo folder (unless it is already
    there) and return the path to use from now on. A file that cannot be
    read is returned unchanged — the renderer reports it as missing."""
    src = Path(source)
    folder = art_dir / PHOTO_DIR
    if _is_inside(src, folder) or not src.is_file():
        return source
    try:
        digest = hashlib.sha256(src.read_bytes()).hexdigest()[:12]
        target = folder / f"{slot}-{digest}{src.suffix.lower() or '.png'}"
        if not target.is_file():
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        for stale in folder.glob(f"{slot}-*"):
            if stale != target:
                stale.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not keep cover photo %s for %s: %s", source, slot, exc)
        return source
    return str(target)


def _photo_to_json(art_dir: Path, photo: PhotoSetup) -> dict[str, Any]:
    if photo.path is None:
        file = HOST_PHOTO
    elif _is_inside(Path(photo.path), art_dir):
        file = Path(photo.path).resolve().relative_to(art_dir.resolve()).as_posix()
    else:
        file = photo.path
    return {"file": file, "focus": list(photo.focus), "zoom": photo.zoom}


def save_setup(art_dir: Path, setup: CoverSetup) -> None:
    """Write *setup* atomically. Photo paths inside *art_dir* are stored
    relative to it so the folder can move with the data directory."""
    data = {
        "layout": setup.layout,
        "variant": setup.variant,
        "shuffle": setup.shuffle,
        "title": setup.title,
        "names": setup.names,
        "photos": {
            slot: _photo_to_json(art_dir, photo) for slot, photo in setup.photos.items()
        },
    }
    try:
        save_overlay(setup_path(art_dir), data)
    except OSError as exc:
        logger.warning("Could not save the cover setup in %s: %s", art_dir, exc)


def _photo_from_json(art_dir: Path, raw: object) -> PhotoSetup | None:
    if not isinstance(raw, dict):
        return None
    file = raw.get("file")
    if not isinstance(file, str) or not file:
        return None
    path: str | None
    if file == HOST_PHOTO:
        path = None
    else:
        candidate = Path(file)
        if not candidate.is_absolute():
            candidate = art_dir / candidate
        if not candidate.is_file():
            return None
        path = str(candidate)
    focus = raw.get("focus")
    try:
        fx, fy = (float(v) for v in focus) if isinstance(focus, list) else (0.5, 0.5)
        zoom = float(raw.get("zoom", 1.0))
    except (TypeError, ValueError):
        fx, fy, zoom = 0.5, 0.5, 1.0
    return PhotoSetup(path, (fx, fy), zoom)


def load_setup(art_dir: Path) -> CoverSetup | None:
    """The stored setup, or ``None`` when there is none (or it is broken).
    Photos whose file has gone are left out."""
    data = load_overlay(setup_path(art_dir))
    layout, variant = data.get("layout"), data.get("variant")
    if not isinstance(layout, str) or not isinstance(variant, str):
        return None
    shuffle = data.get("shuffle")
    raw_photos = data.get("photos")
    photos: dict[str, PhotoSetup] = {}
    if isinstance(raw_photos, dict):
        for slot in PHOTO_SLOTS:
            photo = _photo_from_json(art_dir, raw_photos.get(slot))
            if photo is not None:
                photos[slot] = photo
    return CoverSetup(
        layout=layout,
        variant=variant,
        shuffle=shuffle if isinstance(shuffle, int) and shuffle >= 0 else 0,
        title=str(data.get("title") or ""),
        names=str(data.get("names") or ""),
        photos=photos,
    )
