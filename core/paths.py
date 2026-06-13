"""
Whispered – Application paths
Single source of truth for where the app stores its data.

Existing installations that already have data in ``~/.whisper-fedora``
continue using that directory so no migration is needed.  Fresh installs
get the XDG-compliant location (``$XDG_DATA_HOME/Whispered`` or
``~/.local/share/Whispered`` on Linux, matching what the logger already
uses for its log files).
"""

from __future__ import annotations

import os
from pathlib import Path

_LEGACY_DIR = Path.home() / ".whisper-fedora"
_APP_NAME = "Whispered"


def data_dir() -> Path:
    """Return the application data directory, creating it if absent.

    Returns the legacy ``~/.whisper-fedora`` when it already exists so
    that existing user data is found without migration.  New installations
    receive the XDG-compliant path.
    """
    if _LEGACY_DIR.exists():
        return _LEGACY_DIR
    xdg = os.environ.get("XDG_DATA_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    path = base / _APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path
