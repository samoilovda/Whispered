"""Platform-aware locations for Whispered data and bundled resources."""

from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path

_APP_NAME = "Whispered"
_LEGACY_DIR = Path.home() / ".whisper-fedora"
_UNSAFE_STEM_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _ensure_private_dir(path: Path) -> Path:
    """Create an application-data directory with owner-only POSIX access."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            path.chmod(0o700)
        except OSError:
            # Some mounted/network filesystems do not expose POSIX modes.
            pass
    return path


def _previous_xdg_dir() -> Path:
    """Return the data directory used by versions before platform support."""
    xdg = os.environ.get("XDG_DATA_HOME", "")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / _APP_NAME


def _platform_data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / _APP_NAME
        return Path.home() / "AppData" / "Local" / _APP_NAME
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / _APP_NAME
    return _previous_xdg_dir()


def data_dir() -> Path:
    """Return the writable user-data directory, creating it when needed.

    Existing Linux/XDG and legacy installations keep their current location so
    an update never makes a user's history appear to disappear. New Windows
    installs use ``%LOCALAPPDATA%\\Whispered``; macOS uses Application Support.
    """
    if platform.system() == "Linux" and _LEGACY_DIR.exists():
        path = _LEGACY_DIR
    else:
        old_xdg = _previous_xdg_dir()
        system = platform.system()
        path = old_xdg if system == "Darwin" and old_xdg.exists() else _platform_data_dir()
    return _ensure_private_dir(path)


def config_dir() -> Path:
    return data_dir()


def config_path() -> Path:
    return config_dir() / "config.json"


def history_path() -> Path:
    return data_dir() / "history.db"


def models_dir() -> Path:
    # Keep the long-standing macOS model location. Besides preserving existing
    # downloads, the standalone .app deploys its whisper runtime alongside it.
    if platform.system() == "Darwin":
        path = Path.home() / "Library" / "Application Support" / _APP_NAME / "models"
    else:
        path = data_dir() / "models"
    return _ensure_private_dir(path)


def logs_dir() -> Path:
    # Logger historically used Application Support on macOS; do not split an
    # existing user's diagnostics across two locations during this migration.
    if platform.system() == "Darwin":
        path = Path.home() / "Library" / "Application Support" / _APP_NAME / "logs"
    else:
        path = data_dir() / "logs"
    return _ensure_private_dir(path)


def output_dir() -> Path:
    path = data_dir() / "output"
    return _ensure_private_dir(path)


def artifact_dir(record_id: int | str, source: Path | str) -> Path:
    """Per-source output directory, scoped by history record id.

    Two different source files that happen to share a name (two unrelated
    ``interview.mp4``, one from each of two folders) used to both resolve to
    ``output_dir() / "interview"`` — later artifacts silently overwrote or
    mixed with earlier ones. Suffixing with the history record id keeps
    every transcription's outputs in their own directory regardless of how
    many source files share a stem.

    This is the short-term fix (see docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md,
    R5) ahead of a full Artifact/manifest model; existing output
    directories from before this change are left in place and not migrated.
    """
    stem = _UNSAFE_STEM_CHARS.sub("_", Path(source).stem).strip(". ") or "output"
    dir_name = f"{stem}-{record_id}"
    candidate = (output_dir() / dir_name).resolve()
    root = output_dir().resolve()
    if root not in candidate.parents and candidate != root:
        # A pathological stem could not plausibly escape the sanitizer
        # above, but refuse rather than write outside output_dir().
        raise ValueError(f"resolved artifact directory escapes output_dir(): {candidate}")
    return candidate


def runtime_dir() -> Path:
    path = data_dir() / "runtime"
    return _ensure_private_dir(path)


def resource_path(relative: str | Path) -> Path:
    """Resolve a read-only application resource in source or PyInstaller mode."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parents[1]
    return base / relative


def macos_bundle_path() -> Path | None:
    """Return the enclosing ``.app`` bundle for a frozen macOS build."""
    if not (getattr(sys, "frozen", False) and sys.platform == "darwin"):
        return None
    executable = Path(sys.executable).resolve()
    # A PyInstaller macOS app launches from ``Contents/MacOS/<binary>``.
    if executable.parent.name == "MacOS" and executable.parent.parent.name == "Contents":
        return executable.parent.parent.parent
    return None
