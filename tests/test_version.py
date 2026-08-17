"""Version stays in one place. See docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md,
R9 — before version.py/pyproject.toml existed, three packaging files each
carried their own hardcoded version and had already drifted (0.1.0 in the
two Windows files, 1.0.0 in the AppImage one). These tests don't generate
those files from pyproject.toml (that's further R9 follow-up); they just
make a future drift a failing test instead of a silent inconsistency.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def test_module_version_matches_pyproject():
    import version as version_module

    assert version_module.__version__ == _pyproject_version()


def test_module_fallback_constant_matches_pyproject():
    """The frozen-build fallback (used when pyproject.toml isn't bundled)
    must be bumped alongside pyproject.toml, not left stale."""
    import version as version_module

    assert version_module._FALLBACK == _pyproject_version()


def test_windows_version_info_matches_pyproject():
    content = (ROOT / "packaging/windows/version_info.txt").read_text(encoding="utf-8")
    expected = _pyproject_version()
    assert f"StringStruct('FileVersion', '{expected}')" in content
    assert f"StringStruct('ProductVersion', '{expected}')" in content


def test_windows_installer_version_matches_pyproject():
    content = (ROOT / "packaging/windows/installer.iss").read_text(encoding="utf-8")
    expected = _pyproject_version()
    assert f'#define MyAppVersion "{expected}"' in content


def test_appimage_metainfo_version_matches_pyproject():
    content = (ROOT / "appimage/io.github.whispered.metainfo.xml").read_text(encoding="utf-8")
    expected = _pyproject_version()
    match = re.search(r'<release version="([^"]+)"', content)
    assert match is not None, "no <release version=...> found in metainfo.xml"
    assert match.group(1) == expected


def test_pyproject_version_is_a_plausible_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", _pyproject_version())
