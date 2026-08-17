"""Canonical application version.

See docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R9: before this existed, three
different files each carried their own hardcoded version
(packaging/windows/version_info.txt, packaging/windows/installer.iss,
appimage/io.github.whispered.metainfo.xml) and had already drifted out of
sync (0.1.0 in the two Windows files, 1.0.0 in the AppImage one).

Reads pyproject.toml's [project].version at import time for source/dev
runs. PyInstaller does not bundle pyproject.toml by default, so a frozen
build falls back to _FALLBACK below — keep it equal to pyproject.toml's
version when bumping either. tests/test_version.py checks the packaging
files listed above stay in sync with this value; it does not (yet)
generate them automatically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

_FALLBACK = "0.1.0"


def _read_pyproject_version() -> Optional[str]:
    try:
        import tomllib
    except ModuleNotFoundError:
        return None
    pyproject = Path(__file__).resolve().parent / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except (OSError, KeyError, TypeError, ValueError):
        return None


__version__ = _read_pyproject_version() or _FALLBACK
