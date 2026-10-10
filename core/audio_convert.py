"""FFmpeg conversion of any media file to the 16 kHz mono WAV whisper reads.

Qt-free, so core/multitrack_audio.py and the batch transcriber share it.
"""

from __future__ import annotations

import os
import subprocess
import tempfile


class MediaConversionError(RuntimeError):
    """FFmpeg was available but could not produce a usable WAV file."""


class FFmpegUnavailableError(MediaConversionError):
    """No FFmpeg executable could be resolved for a required conversion."""


def convert_to_wav(input_path: str) -> str:
    """
    Convert audio/video file to WAV format using FFmpeg.
    Return a unique temporary WAV path or raise a diagnostic error.
    """
    from core.external_tools import resolve_tool
    ffmpeg = resolve_tool("ffmpeg")
    if not ffmpeg:
        raise FFmpegUnavailableError("FFmpeg is not installed")

    # Reserve a unique temporary filename.  A deterministic name here lets
    # concurrent transcriptions of equally named files overwrite each other.
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    with tempfile.NamedTemporaryFile(suffix=".wav", prefix=f"{base_name}_", delete=False) as tmp:
        output_path = tmp.name

    error = "FFmpeg did not produce a usable WAV file"
    try:
        # Convert to 16kHz mono WAV (optimal for Whisper)
        result = subprocess.run([
            ffmpeg, '-y', '-i', input_path,
            '-ar', '16000',  # 16kHz sample rate
            '-ac', '1',       # Mono
            '-c:a', 'pcm_s16le',  # 16-bit PCM
            output_path
        ], capture_output=True, text=True, timeout=3600)

        if (
            result.returncode == 0
            and os.path.exists(output_path)
            and os.path.getsize(output_path) > 44
        ):
            return output_path
        detail = (result.stderr or "").strip()[-600:]
        error = f"FFmpeg exited with code {result.returncode}"
        if detail:
            error += f": {detail}"
    except subprocess.TimeoutExpired:
        error = "FFmpeg conversion timed out after 3600 seconds"
    except Exception as exc:
        error = f"FFmpeg conversion failed: {exc}"

    # FFmpeg did not produce a usable file. Do not leave an empty reservation
    # in the system temporary directory.
    try:
        if os.path.exists(output_path):
            os.unlink(output_path)
    except OSError:
        pass

    raise MediaConversionError(error)
