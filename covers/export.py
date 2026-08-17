"""Lossless/lossy cover exports and reproducible sidecars."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QImage


def sanitize_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in " -_" else "_" for char in value)
    return safe[:80].strip(" ._") or "cover"


def _unique_base(directory: Path, name: str) -> Path:
    base = directory / sanitize_name(name)
    if not any(
        base.with_suffix(suffix).exists() for suffix in (".png", ".jpg", ".cover.json")
    ):
        return base
    index = 2
    while any(
        (directory / f"{base.name}-{index}").with_suffix(suffix).exists()
        for suffix in (".png", ".jpg", ".cover.json")
    ):
        index += 1
    return directory / f"{base.name}-{index}"


def _jpeg_bytes(image: QImage, budget: int) -> bytes:
    best = b""
    low, high = 60, 95
    while low <= high:
        quality = (low + high) // 2
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "JPEG", quality):
            raise RuntimeError("Could not encode cover as JPEG")
        data = bytes(buffer.data())
        if len(data) <= budget:
            best = data
            low = quality + 1
        else:
            high = quality - 1
    if best:
        return best
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "JPEG", 60):
        raise RuntimeError("Could not encode cover as JPEG")
    data = bytes(buffer.data())
    if not data:
        raise RuntimeError("Could not encode cover as JPEG")
    return data


def _temporary_path(directory: Path, target: Path) -> Path:
    fd, value = tempfile.mkstemp(prefix=f".{target.name}.", dir=directory)
    os.close(fd)
    return Path(value)


def export(
    image_16x9: QImage,
    image_9x16: QImage | None,
    dest_dir: str | Path,
    base_name: str,
    *,
    state: dict[str, Any] | None = None,
    jpeg_max_bytes: int = 2_000_000,
    export_shorts: bool = False,
) -> list[Path]:
    if image_16x9.isNull():
        raise ValueError("Cannot export an empty cover image")
    if export_shorts and image_9x16 is not None and image_9x16.isNull():
        raise ValueError("Cannot export an empty Shorts cover image")

    directory = Path(dest_dir)
    directory.mkdir(parents=True, exist_ok=True)
    base = _unique_base(directory, base_name)
    png = base.with_suffix(".png")
    jpg = base.with_suffix(".jpg")
    files = [png, jpg]
    if export_shorts and image_9x16 is not None:
        shorts = base.with_name(f"{base.name}-shorts").with_suffix(".png")
        files.append(shorts)
    sidecar = base.with_suffix(".cover.json")
    payload = dict(state or {})
    payload.setdefault("created", datetime.now(timezone.utc).isoformat())
    files.append(sidecar)

    temporary: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        temporary[png] = _temporary_path(directory, png)
        if not image_16x9.save(str(temporary[png]), "PNG"):
            raise RuntimeError("Could not encode cover as PNG")

        temporary[jpg] = _temporary_path(directory, jpg)
        temporary[jpg].write_bytes(_jpeg_bytes(image_16x9, jpeg_max_bytes))

        if export_shorts and image_9x16 is not None:
            temporary[shorts] = _temporary_path(directory, shorts)
            if not image_9x16.save(str(temporary[shorts]), "PNG"):
                raise RuntimeError("Could not encode Shorts cover as PNG")

        temporary[sidecar] = _temporary_path(directory, sidecar)
        temporary[sidecar].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        for target in files:
            os.replace(temporary[target], target)
            committed.append(target)
        return files
    except Exception:
        for path in temporary.values():
            try:
                path.unlink()
            except OSError:
                pass
        # _unique_base selected targets that did not exist. If committing the
        # prepared set fails halfway, remove only this invocation's files.
        for path in committed:
            try:
                path.unlink()
            except OSError:
                pass
        raise
