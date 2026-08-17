"""Cancellable frame extraction via the project's FFmpeg resolver."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from core.external_tools import resolve_tool
from video_input import ensure_ffmpeg, probe_video

# Per-frame extraction timeout (seconds). FFmpeg decoding a single frame
# from a well-formed file typically completes in <1 s; 15 s is generous
# enough for large/slow files while still bounding a stuck process.
_FRAME_TIMEOUT_S = 15

# Single-frame extraction timeout for extract_frame() (no seek loop needed).
_SINGLE_FRAME_TIMEOUT_S = 30


def _session_dir(directory: str | Path | None = None) -> Path:
    if directory:
        result = Path(directory)
        result.mkdir(parents=True, exist_ok=True)
        return result
    return Path(tempfile.mkdtemp(prefix="whispered-covers-"))


def _run_ffmpeg(command: list[str], output: Path, timeout: float) -> None:
    """Run FFmpeg as a subprocess with a hard timeout.

    Kills the process and removes *output* if the timeout expires or
    the command returns a non-zero exit code.  Always cleans up.

    Raises:
        RuntimeError: on non-zero exit code or timeout.
    """
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _, stderr_bytes = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate()
        except Exception:
            pass
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(
            f"FFmpeg timed out after {timeout}s extracting {output.name}"
        )

    if proc.returncode:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        stderr_text = stderr_bytes.decode(errors="replace").strip()
        raise RuntimeError(stderr_text or "FFmpeg could not extract a frame")


def extract_frame(
    video: str | Path,
    time_sec: float,
    directory: str | Path | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    ensure_ffmpeg()
    ffmpeg = resolve_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is not installed")
    if cancel and cancel():
        raise RuntimeError("Cancelled before extraction")
    output = _session_dir(directory) / f"frame-{time_sec:.3f}.png"
    command = [
        ffmpeg,
        "-v", "error",
        "-i", str(video),
        "-ss", f"{time_sec:.3f}",
        "-frames:v", "1",
        "-y", str(output),
    ]
    _run_ffmpeg(command, output, timeout=_SINGLE_FRAME_TIMEOUT_S)
    return output


def extract_candidates(
    video: str | Path,
    count: int = 12,
    directory: str | Path | None = None,
    progress: Callable[[int, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> list[Path]:
    ensure_ffmpeg()
    ffmpeg = resolve_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is not installed")
    _, duration = probe_video(str(video))
    target = _session_dir(directory)
    times = (
        [(index + 1) * duration / (count + 1) for index in range(count)]
        if duration
        else [float(i) for i in range(count)]
    )
    outputs: list[Path] = []
    for index, timestamp in enumerate(times):
        if cancel and cancel():
            break
        output = target / f"candidate-{index:02d}.png"
        command = [
            ffmpeg,
            "-v", "error",
            "-ss", f"{timestamp:.3f}",
            "-i", str(video),
            "-frames:v", "1",
            "-y", str(output),
        ]
        _run_ffmpeg(command, output, timeout=_FRAME_TIMEOUT_S)
        outputs.append(output)
        if progress:
            progress(round((index + 1) / count * 100), f"Кадр {index + 1} из {count}")
    return outputs
