"""Cancellable frame extraction via the project's FFmpeg resolver."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from core.external_tools import resolve_tool
from video_input import ensure_ffmpeg, probe_video


def _session_dir(directory: str | Path | None = None) -> Path:
    if directory:
        result = Path(directory)
        result.mkdir(parents=True, exist_ok=True)
        return result
    return Path(tempfile.mkdtemp(prefix="whispered-covers-"))


def extract_frame(
    video: str | Path, time_sec: float, directory: str | Path | None = None
) -> Path:
    ensure_ffmpeg()
    ffmpeg = resolve_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is not installed")
    output = _session_dir(directory) / f"frame-{time_sec:.3f}.png"
    command = [
        ffmpeg,
        "-v",
        "error",
        "-i",
        str(video),
        "-ss",
        f"{time_sec:.3f}",
        "-frames:v",
        "1",
        "-y",
        str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(
            completed.stderr.strip() or "FFmpeg could not extract a frame"
        )
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
            "-v",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-y",
            str(output),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode:
            raise RuntimeError(
                completed.stderr.strip() or "FFmpeg could not extract candidates"
            )
        outputs.append(output)
        if progress:
            progress(round((index + 1) / count * 100), f"Кадр {index + 1} из {count}")
    return outputs
