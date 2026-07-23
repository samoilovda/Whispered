"""Whispered - Video Cut: assemble a draft video from kept segments via ffmpeg.

Default path is frame-accurate: each kept segment is extracted with input
fast-seek + re-encode into an MPEG-TS chunk, then the chunks are concatenated
losslessly. This avoids the keyframe-snapping inaccuracy of the concat demuxer's
inpoint/outpoint (which can drift by a whole GOP, i.e. several seconds).

An opt-in `fast=True` mode uses the concat demuxer with `-c copy`: much faster
and lossless, but cuts snap to the nearest keyframe and are NOT frame-accurate.
"""

import os
import subprocess
import tempfile

from video_input import ensure_ffmpeg
from core.external_tools import resolve_tool
from core.logger import get_logger

logger = get_logger(__name__)


class VideoCutError(RuntimeError):
    """Raised when ffmpeg fails to produce a valid output."""


def assemble_draft(
    source_path: str,
    kept_segments,
    output_path: str,
    on_progress=None,
    fast: bool = False,
    crf: int = 18,
    preset: str = "fast",
) -> None:
    """Cut and concatenate kept_segments from source_path into output_path.

    Parameters
    ----------
    source_path   : Original video file to cut from.
    kept_segments : Iterable of objects with .start and .end (seconds, float).
    output_path   : Destination .mp4 path.
    on_progress   : Optional callable(message: str) for status updates.
    fast          : If True, use the concat demuxer with -c copy — fast and
                    lossless but keyframe-snapped (not frame-accurate). On
                    failure it falls back to the accurate re-encode path.
                    Default False = frame-accurate per-segment re-encode.
    crf           : x264 quality for the accurate path (lower = better).
    preset        : x264 speed/quality preset for the accurate path.
    """
    ensure_ffmpeg()

    segs = [s for s in kept_segments if s.end - s.start > 0]
    if not segs:
        raise VideoCutError("No segments to assemble.")

    def _progress(msg: str):
        if on_progress:
            on_progress(msg)
        logger.info(msg)

    if fast:
        try:
            _run_concat_copy(source_path, segs, output_path, _progress)
            _verify_output(output_path)
            _progress(f"Done (fast copy): {output_path}")
            return
        except VideoCutError as exc:
            _progress(f"Fast copy failed ({exc}); falling back to accurate re-encode…")
            if os.path.exists(output_path):
                os.remove(output_path)

    _assemble_accurate(source_path, segs, output_path, _progress, crf, preset)
    _verify_output(output_path)
    _progress(f"Done: {output_path}")


# ---------------------------------------------------------------------------
# Frame-accurate path (default): per-segment re-encode + lossless concat
# ---------------------------------------------------------------------------

def _assemble_accurate(source_path, segments, output_path, progress, crf, preset):
    abs_source = os.path.abspath(source_path)
    total = len(segments)
    with tempfile.TemporaryDirectory() as tmpdir:
        ts_files = []
        for i, seg in enumerate(segments):
            progress(f"Cutting segment {i + 1}/{total}…")
            ts_path = os.path.join(tmpdir, f"seg_{i:04d}.ts")
            cmd = [
                resolve_tool("ffmpeg") or "ffmpeg", "-y",
                "-ss", f"{seg.start:.3f}",
                "-to", f"{seg.end:.3f}",
                "-i", abs_source,
                "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-c:a", "aac", "-b:a", "192k",
                "-avoid_negative_ts", "make_zero",
                "-f", "mpegts",
                ts_path,
            ]
            _run(cmd)
            ts_files.append(ts_path)

        progress("Joining segments…")
        list_path = os.path.join(tmpdir, "concat.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for p in ts_files:
                f.write(f"file '{p}'\n")
        cmd = [
            resolve_tool("ffmpeg") or "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            "-movflags", "+faststart",
            output_path,
        ]
        _run(cmd)


# ---------------------------------------------------------------------------
# Fast path (opt-in): concat demuxer + -c copy (keyframe-snapped)
# ---------------------------------------------------------------------------

def _write_concat_list(source_path: str, segments, tmpdir: str) -> str:
    """Write an ffmpeg concat-demuxer list file and return its path."""
    list_path = os.path.join(tmpdir, "concat_list.txt")
    abs_source = os.path.abspath(source_path)
    with open(list_path, "w", encoding="utf-8") as f:
        for seg in segments:
            f.write(f"file '{abs_source}'\n")
            f.write(f"inpoint {seg.start:.6f}\n")
            f.write(f"outpoint {seg.end:.6f}\n")
    return list_path


def _run_concat_copy(source_path: str, segments, output_path: str, progress):
    """Attempt -c copy concat (fast, lossless, keyframe-snapped)."""
    progress("Assembling with stream copy (fast, keyframe-snapped)…")
    with tempfile.TemporaryDirectory() as tmpdir:
        list_path = _write_concat_list(source_path, segments, tmpdir)
        cmd = [
            resolve_tool("ffmpeg") or "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            output_path,
        ]
        _run(cmd)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run(cmd: list):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        snippet = (result.stderr or "")[-400:]
        raise VideoCutError(f"ffmpeg exited {result.returncode}: …{snippet}")


def _verify_output(path: str):
    if not os.path.exists(path) or os.path.getsize(path) < 1024:
        raise VideoCutError(f"Output file missing or too small: {path}")
