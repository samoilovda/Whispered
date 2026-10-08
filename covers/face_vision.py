"""Faces and their portrait quality via Apple Vision (macOS only).

Runs the native ``whispered-face-helper`` (native/face_quality_helper):
it reports each face's box and Vision's face capture quality — how good
the face is as a photo (sharp, eyes open, facing the camera, lit). Where
the helper is missing (Windows, Linux, a source checkout that did not
build it) ``analyse_faces`` returns ``None`` and callers fall back to
plain sharpness — cover photos keep working without it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from core.logger import get_logger
from core.paths import macos_bundle_path

logger = get_logger(__name__)

HELPER_NAME = "whispered-face-helper"
# Vision handles a frame in tens of milliseconds; this bounds a stuck run.
_TIMEOUT_PER_IMAGE_S = 5.0


@dataclass(frozen=True)
class Face:
    """A face box as fractions of the image (origin top-left) and Vision's
    capture quality, 0..1 (higher is a better portrait)."""

    x: float
    y: float
    w: float
    h: float
    quality: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


def helper_path(project_root: Path | None = None) -> Path:
    """Where the helper lives: inside the .app, or the SwiftPM build of a
    source checkout (``swift build -c release`` in native/face_quality_helper)."""
    bundle = macos_bundle_path()
    if bundle is not None:
        return bundle / "Contents" / "Helpers" / HELPER_NAME
    root = project_root or Path(__file__).resolve().parents[1]
    return root / "native" / "face_quality_helper" / ".build" / "release" / HELPER_NAME


def is_available() -> bool:
    return sys.platform == "darwin" and helper_path().is_file()


def parse_output(text: str) -> dict[str, list[Face]]:
    """The helper's JSON lines as ``{path: faces}``; images it could not
    read are left out."""
    result: dict[str, list[Face]] = {}
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict) or "faces" not in item:
            continue
        faces = []
        for raw in item["faces"]:
            try:
                faces.append(Face(
                    float(raw["x"]), float(raw["y"]), float(raw["w"]), float(raw["h"]),
                    float(raw.get("quality", 0.0)),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        result[str(item.get("path", ""))] = faces
    return result


def analyse_faces(images: Iterable[str | Path]) -> Optional[dict[str, list[Face]]]:
    """Faces per image path (as given), or ``None`` when Vision is not
    available or the helper failed — the caller's cue to fall back."""
    paths = [str(path) for path in images]
    if not paths or not is_available():
        return None
    try:
        completed = subprocess.run(
            [str(helper_path()), *paths],
            capture_output=True, text=True,
            timeout=_TIMEOUT_PER_IMAGE_S * len(paths) + 10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Face helper failed: %s", exc)
        return None
    if completed.returncode != 0:
        logger.warning("Face helper exited with %d: %s", completed.returncode,
                       completed.stderr.strip()[:200])
        return None
    return parse_output(completed.stdout)
