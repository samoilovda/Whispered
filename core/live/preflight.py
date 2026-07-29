"""Preflight checks and resource profiles for the Live UI."""

from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.external_tools import resolve_tool
from core.paths import macos_bundle_path
from core.platform_support import live_system_audio_unavailable_message, supports_live_system_audio


class PreflightStatus(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class ResourceProfile:
    name: str
    memory_gb: float
    partial_interval_seconds: float
    max_pending_per_source: int
    supported: bool
    message: str


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    key: str
    status: PreflightStatus
    message: str


def physical_memory_gb() -> float:
    """Return installed memory without adding a runtime dependency."""
    if platform.system() == "Darwin":
        try:
            value = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"],
                text=True,
                timeout=2,
                stderr=subprocess.DEVNULL,
            )
            return int(value.strip()) / (1024**3)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if platform.system() == "Windows":
        try:
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(status)
            # windll exists only on Windows; this whole branch is guarded by
            # the platform check above and by the except below.
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(  # type: ignore[attr-defined]
                ctypes.byref(status)
            ):
                return float(status.ullTotalPhys) / (1024**3)
        except (AttributeError, OSError):
            pass
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return float(pages * page_size) / (1024**3)
    except (AttributeError, OSError, ValueError):
        return 0.0


def resource_profile(memory_gb: float | None = None) -> ResourceProfile:
    memory = physical_memory_gb() if memory_gb is None else memory_gb
    if memory >= 23:
        return ResourceProfile("recommended", memory, 1.5, 12, True, "24 GB profile")
    if memory >= 15:
        return ResourceProfile("minimum", memory, 3.0, 6, True, "16 GB profile; partial updates are less frequent")
    return ResourceProfile("unsupported", memory, 4.0, 4, False, "At least 16 GB RAM is required for Live")


def default_helper_path(project_root: Path | None = None) -> Path:
    bundle = macos_bundle_path()
    if bundle is not None:
        return bundle / "Contents" / "Helpers" / "whispered-capture-helper"
    root = project_root or Path(__file__).resolve().parents[2]
    return root / "native" / "system_capture_helper" / ".build" / "release" / "whispered-capture-helper"


class LivePreflight:
    """Repeatable checks shown before starting any capture source."""

    def run(
        self,
        *,
        use_mic: bool,
        use_system: bool,
        model_name: str,
        target_available: bool = True,
        helper_path: Path | None = None,
        memory_gb: float | None = None,
    ) -> tuple[PreflightCheck, ...]:
        checks: list[PreflightCheck] = []
        profile = resource_profile(memory_gb)
        checks.append(PreflightCheck(
            "memory",
            PreflightStatus.PASS if profile.supported else PreflightStatus.FAIL,
            profile.message,
        ))
        if not use_mic and not use_system:
            checks.append(PreflightCheck("source", PreflightStatus.FAIL, "Select at least one audio source"))
        else:
            checks.append(PreflightCheck("source", PreflightStatus.PASS, "Audio source selected"))
        if use_mic:
            try:
                from core.recorder import list_input_devices
                devices = list_input_devices()
            except Exception:
                devices = []
            checks.append(PreflightCheck(
                "microphone",
                PreflightStatus.PASS if devices else PreflightStatus.WARNING,
                "Microphone device available" if devices else "Microphone permission/device will be verified on Start",
            ))
        if use_system:
            if not target_available:
                checks.append(PreflightCheck(
                    "target", PreflightStatus.FAIL, "Select a running meeting application"
                ))
            helper = helper_path or default_helper_path()
            if not supports_live_system_audio():
                checks.append(PreflightCheck(
                    "system_audio", PreflightStatus.FAIL, live_system_audio_unavailable_message()
                ))
            elif helper.is_file() and os.access(helper, os.X_OK):
                checks.append(PreflightCheck("system_audio", PreflightStatus.PASS, "ScreenCaptureKit helper is ready; permission is verified on Start"))
            else:
                checks.append(PreflightCheck("system_audio", PreflightStatus.FAIL, f"Build capture helper: {helper.parent.parent.parent / 'README.md'}"))
        from utils import get_models_dir
        try:
            model_dir = Path(get_models_dir())
            candidates = tuple(model_dir.glob(f"*{model_name}*")) if model_name else ()
        except OSError:
            # Preflight is read-only. A restricted profile must report a
            # warning rather than fail while merely checking model presence.
            candidates = ()
        checks.append(PreflightCheck(
            "model",
            PreflightStatus.PASS if candidates else PreflightStatus.WARNING,
            f"Model ready: {model_name}" if candidates else f"Model {model_name} will be downloaded/verified on Start",
        ))
        checks.append(PreflightCheck(
            "ffmpeg",
            PreflightStatus.PASS if resolve_tool("ffmpeg") else PreflightStatus.WARNING,
            "FFmpeg available" if resolve_tool("ffmpeg") else "FFmpeg is optional for Live but required by later media workflows",
        ))
        return tuple(checks)

    @staticmethod
    def can_start(checks: tuple[PreflightCheck, ...]) -> bool:
        return not any(check.status is PreflightStatus.FAIL for check in checks)
