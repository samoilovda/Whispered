"""Qt-free presentation models and privacy-safe Live metric snapshots."""

from __future__ import annotations

from core.live.contracts import SegmentState


class LiveTranscriptModel:
    def __init__(self) -> None:
        self._updates: dict[str, object] = {}

    def accept(self, update) -> bool:
        previous = self._updates.get(update.segment_id)
        if previous and (
            previous.state is SegmentState.FINAL or update.revision <= previous.revision
        ):
            return False
        self._updates[update.segment_id] = update
        return True

    def get(self, segment_id: str):
        return self._updates.get(segment_id)

    def relationships(self) -> dict[str, tuple[str, ...]]:
        updates = list(self._updates.values())
        flags: dict[str, list[str]] = {item.segment_id: [] for item in updates}
        for index, left in enumerate(updates):
            for right in updates[index + 1:]:
                a, b = left.segment, right.segment
                source_a = a.speaker or left.segment_id.split(":", 1)[0]
                source_b = b.speaker or right.segment_id.split(":", 1)[0]
                if source_a == source_b or max(a.start, b.start) >= min(a.end, b.end):
                    continue
                flags[left.segment_id].append("overlap")
                flags[right.segment_id].append("overlap")
                text_a = " ".join(a.text.casefold().split())
                text_b = " ".join(b.text.casefold().split())
                if text_a and text_a == text_b:
                    flags[left.segment_id].append("ambiguous")
                    flags[right.segment_id].append("ambiguous")
        return {key: tuple(dict.fromkeys(value)) for key, value in flags.items()}

    def clear(self) -> None:
        self._updates.clear()


def safe_metrics_snapshot(metrics, session_state: str) -> dict:
    """Return numeric/version health only; never accept content or paths."""
    sources = {}
    for source, stats in metrics.source_stats.items():
        sources[source] = {
            "state": metrics.source_states[source].value,
            "queued_frames": getattr(stats, "queued_frames", 0),
            "dropped_frames": getattr(stats, "dropped_frames", 0),
            "lag_seconds": round(float(getattr(stats, "lag_seconds", 0.0)), 3),
            "discontinuities": getattr(stats, "discontinuities", 0),
        }
    scheduler, asr, drift = metrics.scheduler, metrics.asr, metrics.drift
    return {
        "state": session_state,
        "elapsed_seconds": round(metrics.elapsed_seconds, 2),
        "profile": {
            "name": metrics.profile.name,
            "memory_gb": round(metrics.profile.memory_gb, 1),
            "partial_interval_seconds": metrics.profile.partial_interval_seconds,
        },
        "sources": sources,
        "asr": {
            "submitted": getattr(asr, "submitted", 0),
            "completed": getattr(asr, "completed", 0),
            "failed": getattr(asr, "failed", 0),
            "model_load_seconds": getattr(asr, "model_load_seconds", None),
            "final_latency_p95": getattr(asr, "final_latency_p95", None),
            "queued": getattr(scheduler, "queued", 0),
            "in_flight": getattr(scheduler, "in_flight", 0),
        },
        "clock": {
            "max_drift_ms": (
                round(getattr(drift, "max_abs_drift_seconds", 0.0) * 1000, 3)
                if drift else None
            ),
            "ppm": getattr(drift, "max_abs_drift_ppm", None),
            "within_target": getattr(drift, "within_target", None),
        },
    }
