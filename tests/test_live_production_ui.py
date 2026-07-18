"""Production Live UI contracts that must stay independent of real devices."""

from __future__ import annotations

import json
from types import SimpleNamespace

from core.live.contracts import SegmentState, SegmentUpdate
from core.live.preflight import ResourceProfile
from core.live.presentation import LiveTranscriptModel, safe_metrics_snapshot
from core.live.runtime import LiveRuntime, LiveRuntimeMetrics, SessionState, SourceState
from core.live.target_discovery import discover_targets


def _update(segment_id: str, revision: int, state: SegmentState, source: str,
            text: str, start: float = 1.0, end: float = 2.0) -> SegmentUpdate:
    return SegmentUpdate(
        segment_id,
        revision,
        state,
        SimpleNamespace(start=start, end=end, text=text, speaker=source, words=[]),
    )


def test_live_transcript_final_is_stable():
    model = LiveTranscriptModel()
    assert model.accept(_update("mic:1", 1, SegmentState.PARTIAL, "mic", "one"))
    assert model.accept(_update("mic:1", 2, SegmentState.FINAL, "mic", "one final"))
    assert not model.accept(_update("mic:1", 3, SegmentState.PARTIAL, "mic", "stale"))
    assert model.get("mic:1").segment.text == "one final"


def test_live_overlap_and_ambiguity_have_explicit_flags():
    model = LiveTranscriptModel()
    model.accept(_update("mic:1", 1, SegmentState.FINAL, "mic", "same", 1, 3))
    model.accept(_update("system:1", 1, SegmentState.FINAL, "system", "same", 2, 4))
    assert model.relationships()["mic:1"] == ("overlap", "ambiguous")


def test_runtime_partial_failure_keeps_surviving_source_running():
    runtime = LiveRuntime()
    runtime._states = {"mic": SourceState.RUNNING, "system": SourceState.RUNNING}
    runtime._worker_ready = True
    runtime._set_session_state(SessionState.RUNNING)
    runtime._source_error("system", "capture ended")
    assert runtime.session_state is SessionState.RUNNING
    assert runtime._states["mic"] is SourceState.RUNNING
    assert runtime._states["system"] is SourceState.FAILED


def test_diagnostics_serializer_excludes_sensitive_content():
    stats = SimpleNamespace(queued_frames=1, dropped_frames=0, lag_seconds=0.2)
    metrics = LiveRuntimeMetrics(
        elapsed_seconds=12.0,
        source_states={"mic": SourceState.RUNNING},
        source_stats={"mic": stats},
        scheduler=SimpleNamespace(queued=0, in_flight=0),
        drift=SimpleNamespace(max_abs_drift_seconds=0.01, max_abs_drift_ppm=3,
                              within_target=True),
        asr=SimpleNamespace(submitted=2, completed=2, failed=0,
                            model_load_seconds=1.1, final_latency_p95=0.8),
        profile=ResourceProfile("recommended", 24, 1.5, 12, True, "profile"),
    )
    report = json.dumps(safe_metrics_snapshot(metrics, "running"))
    assert json.loads(report)["sources"]["mic"]["state"] == "running"
    for sensitive in ("transcript", "/Users/person/audio.wav", "secret-key"):
        assert sensitive not in report


def test_target_discovery_prioritizes_zoom(tmp_path, monkeypatch):
    helper = tmp_path / "helper"
    helper.write_text("binary")
    helper.chmod(0o755)
    monkeypatch.setattr("core.live.target_discovery.platform.system", lambda: "Darwin")
    payload = [
        {"display_name": "Browser", "bundle_id": "com.browser", "process_id": 8},
        {"display_name": "Zoom", "bundle_id": "us.zoom.xos", "process_id": 7},
    ]
    monkeypatch.setattr(
        "core.live.target_discovery._run_command",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
    )
    targets = discover_targets(helper)
    assert targets[0].bundle_id == "us.zoom.xos"
    assert targets[0].capture_target().process_id == 7


def test_target_discovery_empty_error_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr("core.live.target_discovery.platform.system", lambda: "Linux")
    assert discover_targets() == ()

    helper = tmp_path / "helper"
    helper.write_text("binary")
    helper.chmod(0o755)
    monkeypatch.setattr("core.live.target_discovery.platform.system", lambda: "Darwin")
    monkeypatch.setattr("core.live.target_discovery._run_command", lambda *args, **kwargs: None)
    assert discover_targets(helper) == ()

    monkeypatch.setattr(
        "core.live.target_discovery._run_command",
        lambda *args, **kwargs: SimpleNamespace(returncode=3, stdout="", stderr="denied"),
    )
    try:
        discover_targets(helper)
    except RuntimeError as exc:
        assert "denied" in str(exc)
    else:
        raise AssertionError("Discovery errors must stay distinguishable from an empty list")
