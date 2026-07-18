"""Live-transcription primitives kept separate from the batch pipeline."""

from core.live.contracts import (
    AudioFrame,
    LiveSegmentRevisions,
    SegmentState,
    SegmentUpdate,
    SpeechTurn,
)
from core.live.audio_buffer import (
    BoundedAudioRing,
    CancellationToken,
    MonotonicTimestamp,
    RingStats,
)
from core.live.vad import EnergyVAD, PerSourceVAD, VADConfig, pcm_rms

__all__ = [
    "AudioFrame",
    "LiveSegmentRevisions",
    "SegmentState",
    "SegmentUpdate",
    "SpeechTurn",
    "BoundedAudioRing",
    "CancellationToken",
    "MonotonicTimestamp",
    "RingStats",
    "MicSource",
    "EnergyVAD",
    "PerSourceVAD",
    "VADConfig",
    "pcm_rms",
]


def __getattr__(name: str):
    """Load the Qt-backed adapter only when the caller asks for it."""
    if name == "MicSource":
        from core.live.mic_source import MicSource

        return MicSource
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
