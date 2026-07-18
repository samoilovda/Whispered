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
from core.live.reconciler import LiveSegmentReconciler, ReconcileStats, StablePrefixTracker
from core.live.system_capture_protocol import (
    CaptureLifecycle,
    CaptureTarget,
    FrameDecoder,
    IPCFrame,
    MessageType,
    ProtocolError,
    audio_frame,
    encode_frame,
    hello_frame,
    start_frame,
    stop_frame,
)
from core.live.system_audio_source import SystemAudioSource

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
    "LiveSegmentReconciler",
    "ReconcileStats",
    "StablePrefixTracker",
    "CaptureLifecycle",
    "CaptureTarget",
    "FrameDecoder",
    "IPCFrame",
    "MessageType",
    "ProtocolError",
    "audio_frame",
    "encode_frame",
    "hello_frame",
    "start_frame",
    "stop_frame",
    "SystemAudioSource",
    "PersistentWhisperWorker",
    "LiveASRResult",
    "LiveASRMetrics",
]


def __getattr__(name: str):
    """Load the Qt-backed adapter only when the caller asks for it."""
    if name == "MicSource":
        from core.live.mic_source import MicSource

        return MicSource
    if name in {"PersistentWhisperWorker", "LiveASRResult", "LiveASRMetrics"}:
        from core.live.asr_worker import LiveASRMetrics, LiveASRResult, PersistentWhisperWorker

        return {
            "PersistentWhisperWorker": PersistentWhisperWorker,
            "LiveASRResult": LiveASRResult,
            "LiveASRMetrics": LiveASRMetrics,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
