"""Resolve FFmpeg tools consistently in source and frozen builds."""

from __future__ import annotations

import platform
import shutil
from pathlib import Path

from core.paths import resource_path


def tool_name(name: str) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


def resolve_tool(name: str) -> str | None:
    """Return an absolute bundled or PATH-resolved executable path."""
    executable = tool_name(name)
    bundled = resource_path(Path("tools") / executable)
    if bundled.is_file():
        return str(bundled)
    found = shutil.which(executable) or shutil.which(name)
    return str(Path(found).resolve()) if found else None


def ffmpeg_install_hint() -> str:
    if platform.system() == "Windows":
        return "Install FFmpeg and add its bin directory to PATH."
    if platform.system() == "Darwin":
        return "brew install ffmpeg"
    if shutil.which("dnf"):
        return "sudo dnf install ffmpeg"
    return "sudo apt install ffmpeg"
