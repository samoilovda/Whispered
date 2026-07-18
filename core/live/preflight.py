"""Preflight checks and resource profiles for the Live UI."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


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
            helper = helper_path or default_helper_path()
            if platform.system() != "Darwin":
                checks.append(PreflightCheck("system_audio", PreflightStatus.FAIL, "System audio currently requires macOS"))
            elif helper.is_file() and os.access(helper, os.X_OK):
                checks.append(PreflightCheck("system_audio", PreflightStatus.PASS, "ScreenCaptureKit helper is ready; permission is verified on Start"))
            else:
                checks.append(PreflightCheck("system_audio", PreflightStatus.FAIL, f"Build capture helper: {helper.parent.parent.parent / 'README.md'}"))
        from utils import get_models_dir
        model_dir = Path(get_models_dir())
        candidates = tuple(model_dir.glob(f"*{model_name}*")) if model_name else ()
        checks.append(PreflightCheck(
            "model",
            PreflightStatus.PASS if candidates else PreflightStatus.WARNING,
            f"Model ready: {model_name}" if candidates else f"Model {model_name} will be downloaded/verified on Start",
        ))
        checks.append(PreflightCheck(
            "ffmpeg",
            PreflightStatus.PASS if shutil.which("ffmpeg") else PreflightStatus.WARNING,
            "FFmpeg available" if shutil.which("ffmpeg") else "FFmpeg is optional for Live but required by later media workflows",
        ))
        return tuple(checks)

    @staticmethod
    def can_start(checks: tuple[PreflightCheck, ...]) -> bool:
        return not any(check.status is PreflightStatus.FAIL for check in checks)
