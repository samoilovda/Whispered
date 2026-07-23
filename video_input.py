"""Whispered - Video Input helpers: ffmpeg/ffprobe checks and media probing."""

import json
import subprocess

from core.logger import get_logger
from core.external_tools import ffmpeg_install_hint, resolve_tool

logger = get_logger(__name__)


class FFmpegNotFoundError(RuntimeError):
    """Raised when ffmpeg or ffprobe is not found on the system."""


def _install_hint() -> str:
    return ffmpeg_install_hint()


def ensure_ffmpeg() -> None:
    """Raise FFmpegNotFoundError with a platform-appropriate install hint if ffmpeg is missing."""
    if not resolve_tool("ffmpeg"):
        raise FFmpegNotFoundError(
            f"FFmpeg is not installed.\n\nInstall it with:\n  {_install_hint()}\n\n"
            "Then restart the application."
        )


def ensure_ffprobe() -> None:
    """Raise FFmpegNotFoundError if ffprobe is missing (it ships with ffmpeg)."""
    if not resolve_tool("ffprobe"):
        raise FFmpegNotFoundError(
            f"ffprobe is not installed (it is part of FFmpeg).\n\nInstall it with:\n  {_install_hint()}\n\n"
            "Then restart the application."
        )


def probe_video(path: str) -> tuple[float, float]:
    """Return (fps, duration_seconds) for a video/audio file via ffprobe.

    fps is parsed from the first video stream's r_frame_rate field
    (e.g. "30000/1001" → 29.97).  Falls back to (30.0, 0.0) on any
    parsing failure so callers can proceed without crashing.

    Raises FFmpegNotFoundError if ffprobe is not installed.
    """
    ensure_ffprobe()

    try:
        ffprobe = resolve_tool("ffprobe")
        if not ffprobe:
            raise FFmpegNotFoundError("ffprobe is not installed")
        result = subprocess.run(
            [
                ffprobe,
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-show_format",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("ffprobe returned non-zero for %s: %s", path, result.stderr[:200])
            return 30.0, 0.0

        data = json.loads(result.stdout)
    except Exception as exc:
        logger.warning("probe_video failed for %s: %s", path, exc)
        return 30.0, 0.0

    # --- fps from first video stream ---
    fps = 30.0
    try:
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                r_rate = stream.get("r_frame_rate", "")
                if "/" in r_rate:
                    num, den = r_rate.split("/", 1)
                    if int(den) > 0:
                        fps = int(num) / int(den)
                break
    except Exception as exc:
        logger.warning("Could not parse fps from ffprobe output: %s", exc)

    # --- duration from format block, fallback to stream ---
    duration = 0.0
    try:
        fmt = data.get("format", {})
        if "duration" in fmt:
            duration = float(fmt["duration"])
        else:
            for stream in data.get("streams", []):
                if "duration" in stream:
                    duration = float(stream["duration"])
                    break
    except Exception as exc:
        logger.warning("Could not parse duration from ffprobe output: %s", exc)

    return fps, duration
