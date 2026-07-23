"""Small, testable declarations of platform-specific product capabilities."""

from __future__ import annotations

import platform


def is_windows() -> bool:
    return platform.system() == "Windows"


def supports_live_system_audio() -> bool:
    """The shipped system-audio adapter is a macOS ScreenCaptureKit helper."""
    return platform.system() == "Darwin"


def live_system_audio_unavailable_message() -> str:
    if is_windows():
        return "System audio capture is not available on Windows yet."
    return "System audio currently requires macOS."
