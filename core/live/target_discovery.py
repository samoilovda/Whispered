"""Read-only discovery of ScreenCaptureKit shareable applications."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from core.live.preflight import default_helper_path
from core.live.system_capture_protocol import CaptureTarget


@dataclass(frozen=True, slots=True)
class DiscoveredTarget:
    display_name: str
    bundle_id: str
    process_id: int | None = None
    window_id: int | None = None

    def capture_target(self) -> CaptureTarget:
        return CaptureTarget(
            bundle_id=self.bundle_id or None,
            process_id=self.process_id,
            window_id=self.window_id,
        )


def discover_targets(helper_path: Path | None = None, cancelled=None) -> tuple[DiscoveredTarget, ...]:
    """List targets without starting capture or requesting recording permission."""
    if platform.system() != "Darwin":
        return ()
    helper = helper_path or default_helper_path()
    if helper.is_file() and os.access(helper, os.X_OK):
        completed = _run_command([str(helper), "--list-targets"], 5, cancelled)
        if completed is None:
            return ()
        if completed.returncode == 0:
            payload = json.loads(completed.stdout or "[]")
            targets = tuple(_target(item) for item in payload if item.get("bundle_id"))
            return tuple(sorted(targets, key=_sort_key))
        raise RuntimeError(completed.stderr.strip() or "Target discovery failed")
    return _zoom_process_fallback(cancelled)


def _target(item: dict) -> DiscoveredTarget:
    return DiscoveredTarget(
        display_name=str(item.get("display_name") or item.get("bundle_id") or "Application"),
        bundle_id=str(item.get("bundle_id") or ""),
        process_id=int(item["process_id"]) if item.get("process_id") is not None else None,
        window_id=int(item["window_id"]) if item.get("window_id") is not None else None,
    )


def _sort_key(target: DiscoveredTarget) -> tuple[bool, str]:
    return (target.bundle_id != "us.zoom.xos", target.display_name.casefold())


def _zoom_process_fallback(cancelled=None) -> tuple[DiscoveredTarget, ...]:
    """Safe interim fallback when the helper has not been built yet."""
    completed = _run_command(["pgrep", "-x", "zoom.us"], 2, cancelled)
    if completed is None:
        return ()
    if completed.returncode != 0 or not completed.stdout.strip():
        return ()
    pid = int(completed.stdout.splitlines()[0])
    return (DiscoveredTarget("Zoom", "us.zoom.xos", process_id=pid),)


def _run_command(command: list[str], timeout: float, cancelled=None):
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancelled and cancelled():
            process.terminate()
            process.wait(timeout=1)
            return None
        try:
            stdout, stderr = process.communicate(timeout=0.1)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            continue
    process.terminate()
    try:
        stdout, stderr = process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
