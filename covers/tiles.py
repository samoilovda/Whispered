"""Zoom gallery tile detection and candidate scoring without OpenCV."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from core.logger import get_logger

logger = get_logger(__name__)
_warned_no_onnx = False


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def area(self) -> int:
        return self.w * self.h

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


def _runs(mask: np.ndarray, minimum: int = 4) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    start = None
    for index, active in enumerate(np.r_[mask, False]):
        if active and start is None:
            start = index
        elif not active and start is not None:
            if index - start >= minimum:
                result.append((start, index))
            start = None
    return result


def _cells(length: int, gutters: list[tuple[int, int]]) -> list[tuple[int, int]]:
    edges = [0]
    for start, end in gutters:
        if start > edges[-1]:
            edges.append(start)
        edges.append(end)
    edges.append(length)
    return [
        (edges[i], edges[i + 1])
        for i in range(0, len(edges) - 1, 2)
        if edges[i + 1] - edges[i] > 2
    ]


def detect_tiles(image_rgb: np.ndarray) -> list[Rect]:
    if image_rgb.ndim != 3 or image_rgb.shape[2] < 3:
        raise ValueError("image_rgb must have shape H×W×3")
    gray = image_rgb[..., :3].astype(np.float32).mean(axis=2)
    height, width = gray.shape
    row_var, col_var = gray.var(axis=1), gray.var(axis=0)
    row_mean, col_mean = gray.mean(axis=1), gray.mean(axis=0)
    # Gallery gutters are uniform. Derive their color from the most uniform
    # rows/columns rather than frame edges (tiles commonly touch those edges).
    variance_threshold = max(
        4.0, min(64.0, float(np.percentile(np.r_[row_var, col_var], 10)))
    )
    uniform_means = np.r_[
        row_mean[row_var <= variance_threshold], col_mean[col_var <= variance_threshold]
    ]
    background = float(np.median(uniform_means)) if uniform_means.size else 0.0
    tolerance = max(8.0, float(gray.std()) * 0.15)
    horizontal = _runs(
        (row_var <= variance_threshold) & (np.abs(row_mean - background) <= tolerance)
    )
    vertical = _runs(
        (col_var <= variance_threshold) & (np.abs(col_mean - background) <= tolerance)
    )
    # Do not treat outer letterboxing as an internal grid separator.
    horizontal = [(a, b) for a, b in horizontal if a > 0 and b < height]
    vertical = [(a, b) for a, b in vertical if a > 0 and b < width]
    if not horizontal and not vertical:
        return [Rect(0, 0, width, height)]
    rows, columns = _cells(height, horizontal), _cells(width, vertical)
    found = [Rect(x0, y0, x1 - x0, y1 - y0) for y0, y1 in rows for x0, x1 in columns]
    found = [
        rect
        for rect in found
        if rect.area >= width * height * 0.03 and 1.2 <= rect.w / rect.h <= 2.2
    ]
    return found or [Rect(0, 0, width, height)]


def detect_faces(image_rgb: np.ndarray, model_path: str | None = None) -> list[Rect]:
    global _warned_no_onnx
    try:
        import onnxruntime as ort  # noqa: F401
    except ImportError:
        if not _warned_no_onnx:
            logger.info("onnxruntime is unavailable; face detection disabled")
            _warned_no_onnx = True
        return []
    if not model_path:
        return []
    # Model-specific decoding is kept optional; callers still have manual tile selection.
    return []


def _contains(container: Rect, inner: Rect) -> bool:
    x, y = inner.center
    return (
        container.x <= x <= container.x + container.w
        and container.y <= y <= container.y + container.h
    )


def _sharpness(gray: np.ndarray) -> float:
    if min(gray.shape) < 3:
        return 0.0
    lap = (
        -4 * gray[1:-1, 1:-1]
        + gray[:-2, 1:-1]
        + gray[2:, 1:-1]
        + gray[1:-1, :-2]
        + gray[1:-1, 2:]
    )
    return float(lap.var())


def pick_tile(
    tiles: Iterable[Rect],
    faces: Iterable[Rect],
    exclude: Rect | None = None,
    image_rgb: np.ndarray | None = None,
) -> Rect | None:
    candidates = [tile for tile in tiles if tile != exclude]
    if not candidates:
        return None
    face_list = list(faces)
    frame_center = (
        (image_rgb.shape[1] / 2, image_rgb.shape[0] / 2)
        if image_rgb is not None
        else candidates[0].center
    )

    def score(tile: Rect) -> tuple[float, float, float, float]:
        inside = [face for face in face_list if _contains(tile, face)]
        face_area = max((face.area for face in inside), default=0)
        sharp = 0.0
        if image_rgb is not None:
            crop = image_rgb[
                tile.y : tile.y + tile.h, tile.x : tile.x + tile.w, :3
            ].mean(axis=2)
            sharp = _sharpness(crop)
        cx, cy = tile.center
        distance = (cx - frame_center[0]) ** 2 + (cy - frame_center[1]) ** 2
        return bool(inside), face_area, sharp, -distance

    return max(candidates, key=score)


def score_frame(image_rgb: np.ndarray, faces: Iterable[Rect] = ()) -> float:
    gray = image_rgb[..., :3].astype(np.float32).mean(axis=2)
    return _sharpness(gray) * (2.0 if list(faces) else 1.0)
