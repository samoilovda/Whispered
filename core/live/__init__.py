"""Live-transcription primitives kept separate from the batch pipeline.

Names are resolved lazily (PEP 562): ``from core.live import X`` loads only
X's module. Importing a pure module such as ``core.live.contracts`` or
``core.live.vad`` (core/multitrack_* do) must not pull in the Qt-based
capture sources and runtime.
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS: dict[str, str] = {
    "AudioFrame": "core.live.contracts",
    "BufferedSpeechTurn": "core.live.contracts",
    "LiveSegmentRevisions": "core.live.contracts",
    "SegmentState": "core.live.contracts",
    "SegmentUpdate": "core.live.contracts",
    "SpeechTurn": "core.live.contracts",
    "BoundedAudioRing": "core.live.audio_buffer",
    "CancellationToken": "core.live.audio_buffer",
    "MonotonicTimestamp": "core.live.audio_buffer",
    "RingStats": "core.live.audio_buffer",
    "MicSource": "core.live.mic_source",
    "EnergyVAD": "core.live.vad",
    "PerSourceVAD": "core.live.vad",
    "VADConfig": "core.live.vad",
    "pcm_rms": "core.live.vad",
    "LiveSegmentReconciler": "core.live.reconciler",
    "ReconcileStats": "core.live.reconciler",
    "StablePrefixTracker": "core.live.reconciler",
    "CaptureLifecycle": "core.live.system_capture_protocol",
    "CaptureTarget": "core.live.system_capture_protocol",
    "FrameDecoder": "core.live.system_capture_protocol",
    "IPCFrame": "core.live.system_capture_protocol",
    "MessageType": "core.live.system_capture_protocol",
    "ProtocolError": "core.live.system_capture_protocol",
    "audio_frame": "core.live.system_capture_protocol",
    "encode_frame": "core.live.system_capture_protocol",
    "hello_frame": "core.live.system_capture_protocol",
    "start_frame": "core.live.system_capture_protocol",
    "stop_frame": "core.live.system_capture_protocol",
    "SystemAudioSource": "core.live.system_audio_source",
    "ClockMetrics": "core.live.clock_aligner",
    "DriftReport": "core.live.clock_aligner",
    "DualSourceClockAligner": "core.live.clock_aligner",
    "SourceClockAligner": "core.live.clock_aligner",
    "DualQueueASRScheduler": "core.live.asr_scheduler",
    "SchedulerStats": "core.live.asr_scheduler",
    "CHECKS": "core.live.compatibility",
    "SUPPORTED_APPS": "core.live.compatibility",
    "CompatibilityCase": "core.live.compatibility",
    "CompatibilityMatrix": "core.live.compatibility",
    "CompatibilityStatus": "core.live.compatibility",
    "DuplicateDecision": "core.live.echo_detector",
    "EchoDuplicateDetector": "core.live.echo_detector",
    "normalize_text": "core.live.echo_detector",
    "DEFAULT_SOURCE_LABELS": "core.live.overlap_timeline",
    "LiveOverlapTimeline": "core.live.overlap_timeline",
    "SourceSpeakerLabels": "core.live.overlap_timeline",
    "TimelineEntry": "core.live.overlap_timeline",
    "LiveSessionPipeline": "core.live.session_pipeline",
    "PersistentWhisperWorker": "core.live.asr_worker",
    "LiveASRResult": "core.live.asr_worker",
    "LiveASRMetrics": "core.live.asr_worker",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value
