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

import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Tuple

from core.live.contracts import AudioFrame
from core.live.vad import PerSourceVAD, VADConfig, pcm_rms
from core.logger import get_logger
from transcriber import FFmpegUnavailableError, MediaConversionError, _convert_to_wav  # noqa: F401 re-exported for callers

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
    FFmpeg invocation and error surface."""
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
