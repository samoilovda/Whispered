"""Small, testable declarations of platform-specific product capabilities."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path


def is_windows() -> bool:
    return platform.system() == "Windows"


def supports_live_system_audio() -> bool:
    """The shipped system-audio adapter is a macOS ScreenCaptureKit helper."""
    return platform.system() == "Darwin"


def live_system_audio_unavailable_message() -> str:
    if is_windows():
        return "System audio capture is not available on Windows yet."
    if platform.system() == "Linux":
        return "System audio capture is not available on Linux yet; Live runs microphone-only."
    return "System audio currently requires macOS."


def reveal_command(path: "str | Path") -> list[str] | None:
    """Argv that shows *path* in the OS file manager, or None on a platform
    without a known command (Linux file managers differ — the caller falls
    back to opening the containing folder)."""
    target = str(path)
    system = platform.system()
    if system == "Darwin":
        return ["open", "-R", target]
    if system == "Windows":
        return ["explorer", f"/select,{target}"]
    return None


def reveal_in_file_manager(path: "str | Path") -> bool:
    """Select *path* in Finder/Explorer. Returns False when the platform has
    no select-in-folder command; the caller should open the parent folder
    instead. Never uses a shell."""
    argv = reveal_command(path)
    if argv is None:
        return False
    try:
        subprocess.Popen(argv)  # noqa: S603 - fixed argv, no shell
    except OSError:
        return False
    return True
