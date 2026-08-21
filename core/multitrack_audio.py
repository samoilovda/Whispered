"""Per-track speech-window extraction for Zoom multitrack recordings.

Turns one participant's raw audio file into the short speech-only windows
that actually get sent to whisper.cpp — see docs/MULTITRACK_ZOOM_PLAN.ru.md
(M2). A participant's own track is 60-80% silence, so transcribing the whole
thing invites whisper.cpp hallucinations on empty audio; gating on VAD-found
speech both avoids that and cuts the audio actually fed to ASR.

Reuses core/live/vad.py's PerSourceVAD rather than reimplementing turn
detection: it already merges nearby speech, adds pre/post-roll context, and
hands back the exact bounded PCM per turn.
"""

from __future__ import annotations

import bisect
import wave
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, List, Sequence, Tuple

from domain.transcription import Segment

from core.live.contracts import AudioFrame
from core.live.vad import PerSourceVAD, VADConfig, pcm_rms
from core.logger import get_logger

logger = get_logger(__name__)

TARGET_SAMPLE_RATE = 16_000
_FRAME_MS = 30


@dataclass(frozen=True)
class SpeechWindow:
    """One VAD-bounded, context-padded speech interval ready for ASR."""

    start: float
    end: float
    pcm: bytes  # int16 mono PCM at TARGET_SAMPLE_RATE

    @property
    def duration(self) -> float:
        return self.end - self.start


def convert_track_to_wav(path: Path) -> str:
    """Decode a track (m4a or any ffmpeg-readable format) to a 16kHz mono
    WAV, reusing transcriber.py's converter so both pipelines share one
    FFmpeg invocation and error surface.

    Imported lazily: several tests replace ``sys.modules['transcriber']``
    with a lightweight stub (see tests/test_batch_processor.py,
    tests/test_exporters.py), and a module-level import here would bind
    whichever version happened to be installed first in that shared
    process, depending on test collection order.
    """
    from transcriber import _convert_to_wav
    return _convert_to_wav(str(path))


def read_wav_int16_mono(path: str) -> Tuple[bytes, int]:
    """Read a mono int16 WAV file. Raises ValueError if it isn't mono/int16."""
    with wave.open(path, "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM, got {wf.getsampwidth() * 8}-bit")
        if wf.getnchannels() != 1:
            raise ValueError(f"{path}: expected mono, got {wf.getnchannels()} channels")
        sample_rate = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    return pcm, sample_rate


def write_wav(path: str, pcm: bytes, sample_rate: int = TARGET_SAMPLE_RATE) -> None:
    """Write mono int16 PCM to a WAV file (for handing a SpeechWindow to whisper.cpp)."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def wav_to_frames(path: str, source: str, frame_ms: int = _FRAME_MS) -> Iterator[AudioFrame]:
    """Chunk a mono int16 WAV file into fixed-size AudioFrames with
    monotonically increasing timestamps, for feeding into PerSourceVAD."""
    pcm, sample_rate = read_wav_int16_mono(path)
    bytes_per_sample = 2
    frame_samples = max(1, int(sample_rate * frame_ms / 1000))
    frame_bytes = frame_samples * bytes_per_sample
    frame_duration = frame_samples / sample_rate

    sequence = 0
    offset = 0
    timestamp = 0.0
    while offset < len(pcm):
        chunk = pcm[offset: offset + frame_bytes]
        if len(chunk) % 2:
            chunk = chunk[:-1]
        if not chunk:
            break
        yield AudioFrame(
            source=source,
            sequence=sequence,
            source_timestamp=timestamp,
            monotonic_timestamp=timestamp,
            sample_rate=sample_rate,
            pcm=chunk,
        )
        sequence += 1
        offset += frame_bytes
        timestamp += frame_duration


def detect_speech_windows(
    path: str,
    source: str = "track",
    *,
    speech_threshold: float = 0.02,
    silence_threshold: float = 0.01,
    end_silence_seconds: float = 0.6,
    context_padding_seconds: float = 0.3,
    min_duration_seconds: float = 0.4,
) -> List[SpeechWindow]:
    """Detect speech windows in a mono 16kHz WAV file.

    ``context_padding_seconds`` is applied as both pre- and post-roll so
    whisper.cpp doesn't see a window cut off mid-word.
    ``end_silence_seconds`` is the merge gap: speech runs separated by less
    silence than this are joined into one window.
    Windows shorter than ``min_duration_seconds`` after merging are dropped.
    """
    config = VADConfig(
        speech_threshold=speech_threshold,
        silence_threshold=silence_threshold,
        end_silence_seconds=end_silence_seconds,
        pre_roll_seconds=context_padding_seconds,
        post_roll_seconds=context_padding_seconds,
    )
    vad = PerSourceVAD(source=source, config=config)
    windows: List[SpeechWindow] = []

    def _collect(turns):
        for turn in turns:
            buffered = vad.pop_buffered(turn)
            if turn.end - turn.start >= min_duration_seconds:
                windows.append(SpeechWindow(start=turn.start, end=turn.end, pcm=buffered.pcm))

    for frame in wav_to_frames(path, source):
        _collect(vad.feed(frame))
    _collect(vad.flush())

    return windows


def rms_in_range(pcm: bytes, sample_rate: int, start: float, end: float) -> float:
    """Normalized RMS of ``pcm`` (int16 mono) between ``start`` and ``end`` seconds."""
    if end <= start:
        return 0.0
    bytes_per_sample = 2
    start_idx = max(0, int(start * sample_rate) * bytes_per_sample)
    end_idx = min(len(pcm), int(end * sample_rate) * bytes_per_sample)
    if end_idx <= start_idx:
        return 0.0
    chunk = pcm[start_idx:end_idx]
    if len(chunk) % 2:
        chunk = chunk[:-1]
    return pcm_rms(chunk)


def speech_coverage_seconds(windows: List[SpeechWindow]) -> float:
    """Total duration covered by speech windows (post-merge, may include padding overlap)."""
    return sum(w.duration for w in windows)


def wav_duration_seconds(path: str) -> float:
    with wave.open(path, "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


@dataclass(frozen=True)
class GatedTrack:
    """Speech windows of one track concatenated into a single WAV, with the
    bookkeeping needed to map whisper's output timestamps (in this
    concatenated timeline) back to the original track's timeline.

    Concatenating windows into one file — rather than transcribing each
    window as its own whisper.cpp invocation — matters because every
    invocation reloads the ggml model; for a track with dozens of short
    speech windows that dominates wall-clock time.
    """

    pcm: bytes
    sample_rate: int
    # (gated_start, gated_end, original_start) per window, sorted by gated_start.
    mapping: Tuple[Tuple[float, float, float], ...]


def build_gated_track(
    windows: Sequence[SpeechWindow],
    sample_rate: int = TARGET_SAMPLE_RATE,
    gap_seconds: float = 0.3,
) -> GatedTrack:
    """Concatenate speech windows with a short silence gap between them."""
    gap_pcm = b"\x00\x00" * int(sample_rate * gap_seconds)
    pcm_parts: List[bytes] = []
    mapping: List[Tuple[float, float, float]] = []
    cursor = 0.0
    for i, window in enumerate(windows):
        pcm_parts.append(window.pcm)
        gated_end = cursor + window.duration
        mapping.append((cursor, gated_end, window.start))
        cursor = gated_end
        if gap_seconds > 0 and i < len(windows) - 1:
            pcm_parts.append(gap_pcm)
            cursor += gap_seconds
    return GatedTrack(pcm=b"".join(pcm_parts), sample_rate=sample_rate, mapping=tuple(mapping))


def remap_gated_time(gated_time: float, mapping: Sequence[Tuple[float, float, float]]) -> float:
    """Map a timestamp in the gated (concatenated) timeline back to the
    original track's timeline, using the window whose gated range contains
    it. A timestamp that falls in a silence gap (shouldn't normally happen
    for a whisper segment boundary) is clamped to the nearest window edge.
    """
    if not mapping:
        return gated_time
    starts = [m[0] for m in mapping]
    i = bisect.bisect_right(starts, gated_time) - 1
    if i < 0:
        gated_start, _gated_end, original_start = mapping[0]
        return original_start
    gated_start, gated_end, original_start = mapping[i]
    if gated_time <= gated_end:
        return original_start + (gated_time - gated_start)
    # In a gap: clamp to the end of the preceding window rather than
    # extrapolating into the next one's original time.
    return original_start + (gated_end - gated_start)


def remap_segment_to_track_time(seg: Segment, mapping: Sequence[Tuple[float, float, float]]) -> Segment:
    """Return a copy of ``seg`` with start/end mapped from gated-track time
    back to the original track's timeline (see :func:`remap_gated_time`)."""
    return replace(
        seg,
        start=remap_gated_time(seg.start, mapping),
        end=remap_gated_time(seg.end, mapping),
    )


def probe_media_duration(path: str) -> float:
    """Duration in seconds of any ffprobe-readable media file (m4a, mp4, ...).

    Lazily resolves ffprobe for the same reason ``convert_track_to_wav``
    lazily imports ``transcriber`` — keeps this module importable in test
    processes where FFmpeg isn't necessarily on PATH and no track is
    actually being probed.
    """
    import json
    import subprocess

    from core.external_tools import resolve_tool
    ffprobe = resolve_tool("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is not installed")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {(result.stderr or '').strip()[-400:]}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])
