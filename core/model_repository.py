"""
Whispered – Model Repository
Qt-free API for verifying and downloading binary model assets.

Usage::

    from core.model_repository import ModelRepository

    repo = ModelRepository()
    path = repo.ensure(
        "whisper-tiny",
        progress=lambda done, total: print(f"{done}/{total}"),
        cancel=lambda: False,
    )

Integrity contract:
- Existing file is checked by size AND sha256 (if both are in the manifest).
- If the check fails, the file is re-downloaded.
- Download writes to ``<target>.download`` (same directory as the final path).
- On success: ``fsync`` + ``os.replace`` (atomic swap).
- On failure / cancel: ``.download`` file is removed in a ``finally`` block.
- Result file permissions: ``0o600``.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Callable

import requests

from core.logger import get_logger
from core.model_manifest import MANIFEST, ModelEntry
from core.paths import models_dir as _models_dir_fn

logger = get_logger(__name__)

_CHUNK = 65_536  # 64 KiB read chunks
_CONNECT_TIMEOUT = 10  # seconds for initial connection
_READ_TIMEOUT = 600  # seconds for streaming read; matches LM client timeout


class Cancelled(Exception):
    """Raised when the caller's cancel() function returns True."""


class IntegrityError(Exception):
    """Raised when a downloaded (or existing) file fails integrity checks."""


class ModelRepository:
    """Verifies and downloads model files to the private models directory.

    The repository is stateless: it derives paths from ``core.paths`` and
    re-checks every call to ``ensure()``.  Callers cache the returned Path
    themselves if they need repeated access.
    """

    def __init__(self, models_dir: Path | None = None) -> None:
        self._models_dir = Path(models_dir) if models_dir else _models_dir_fn()
        # Ensure the directory exists with private permissions
        self._models_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._models_dir, 0o700)
        except OSError:
            pass  # Best-effort on platforms that don't support it

    # ── Public API ────────────────────────────────────────────────────────

    def ensure(
        self,
        key: str,
        *,
        progress: Callable[[int, int], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> Path:
        """Return the local path to model *key*, downloading if needed.

        Parameters
        ----------
        key:
            Manifest key (e.g. ``"whisper-tiny"``).
        progress:
            Optional callback ``(bytes_done, total_bytes) → None``.
        cancel:
            Optional callable that returns ``True`` when the caller wants
            to abort.  Checked between chunks.

        Raises
        ------
        KeyError:
            Unknown model key.
        Cancelled:
            Caller's ``cancel()`` returned True.
        IntegrityError:
            Downloaded file failed size/sha256 check after retry.
        OSError / requests.RequestException:
            Network or filesystem error not related to integrity.
        """
        entry = MANIFEST.get(key)
        if entry is None:
            raise KeyError(f"Unknown model key: {key!r}")

        target = self._models_dir / entry.filename
        cancel = cancel or (lambda: False)
        progress = progress or (lambda _d, _t: None)

        if self._is_valid(target, entry):
            logger.debug("ModelRepository: %s already valid at %s", key, target)
            return target

        if target.exists():
            logger.warning(
                "ModelRepository: %s exists but failed integrity check — re-downloading",
                key,
            )

        self._download(entry, target, progress=progress, cancel=cancel)
        return target

    def validate(self, key: str) -> bool:
        """Return True if the local file for *key* passes integrity checks."""
        entry = MANIFEST.get(key)
        if entry is None:
            return False
        target = self._models_dir / entry.filename
        return self._is_valid(target, entry)

    # ── Integrity ─────────────────────────────────────────────────────────

    def _is_valid(self, path: Path, entry: ModelEntry) -> bool:
        """Return True if *path* exists and matches the expected size/sha256."""
        if not path.exists():
            return False
        if entry.size_bytes and path.stat().st_size != entry.size_bytes:
            logger.debug(
                "ModelRepository: size mismatch for %s: expected %d, got %d",
                path.name,
                entry.size_bytes,
                path.stat().st_size,
            )
            return False
        if entry.sha256:
            actual = _sha256_file(path)
            if actual != entry.sha256.lower():
                logger.debug(
                    "ModelRepository: sha256 mismatch for %s: expected %s, got %s",
                    path.name,
                    entry.sha256,
                    actual,
                )
                return False
        if not entry.sha256:
            logger.warning(
                "ModelRepository: no sha256 in manifest for %s — "
                "skipping integrity verification",
                entry.key,
            )
        return True

    # ── Download ──────────────────────────────────────────────────────────

    def _download(
        self,
        entry: ModelEntry,
        target: Path,
        *,
        progress: Callable[[int, int], None],
        cancel: Callable[[], bool],
    ) -> None:
        """Download *entry* to *target* atomically.

        Writes to ``<target>.download`` in the same directory, then
        fsync + os.replace on success.  Removes the temp file on any
        failure or cancellation.
        """
        temp = Path(str(target) + ".download")
        try:
            logger.info(
                "ModelRepository: downloading %s → %s", entry.key, target.name
            )
            response = requests.get(
                entry.url,
                stream=True,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            )
            response.raise_for_status()

            total = int(response.headers.get("content-length", 0))
            done = 0
            hasher = hashlib.sha256()

            with open(temp, "wb") as fh:
                for chunk in response.iter_content(chunk_size=_CHUNK):
                    if cancel():
                        raise Cancelled(f"Download of {entry.key!r} cancelled by caller")
                    if not chunk:
                        continue
                    fh.write(chunk)
                    hasher.update(chunk)
                    done += len(chunk)
                    progress(done, total)
                fh.flush()
                os.fsync(fh.fileno())

            # Integrity check on the downloaded file
            if entry.size_bytes and temp.stat().st_size != entry.size_bytes:
                raise IntegrityError(
                    f"{entry.key}: size mismatch after download: "
                    f"expected {entry.size_bytes}, got {temp.stat().st_size}"
                )
            if entry.sha256:
                actual_hex = hasher.hexdigest()
                if actual_hex != entry.sha256.lower():
                    raise IntegrityError(
                        f"{entry.key}: sha256 mismatch after download: "
                        f"expected {entry.sha256}, got {actual_hex}"
                    )

            # Atomic replace
            os.replace(temp, target)
            try:
                os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
            except OSError:
                pass

            logger.info("ModelRepository: %s downloaded and verified", entry.key)

        except BaseException:
            # Remove the temp file on any error (including KeyboardInterrupt)
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass
            raise


# ── Helpers ──────────────────────────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 of *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(65_536)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()
