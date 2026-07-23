"""Platform-aware locations for Whispered data and bundled resources."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

_APP_NAME = "Whispered"
_LEGACY_DIR = Path.home() / ".whisper-fedora"


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
    path.mkdir(parents=True, exist_ok=True)
    return path


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
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    # Logger historically used Application Support on macOS; do not split an
    # existing user's diagnostics across two locations during this migration.
    if platform.system() == "Darwin":
        path = Path.home() / "Library" / "Application Support" / _APP_NAME / "logs"
    else:
        path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def output_dir() -> Path:
    path = data_dir() / "output"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_dir() -> Path:
    path = data_dir() / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_path(relative: str | Path) -> Path:
    """Resolve a read-only application resource in source or PyInstaller mode."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parents[1]
    return base / relative
