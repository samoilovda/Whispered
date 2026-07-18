"""Qt-facing runtime controller used by the L15 Live view."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from core.live.asr_worker import PersistentWhisperWorker
from core.live.mic_source import MicSource
from core.live.preflight import default_helper_path, resource_profile
from core.live.session_pipeline import LiveSessionPipeline
from core.live.system_audio_source import SystemAudioSource
from core.live.system_capture_protocol import CaptureTarget
from core.paths import data_dir


class SourceState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    FAILED = "failed"
    STOPPED = "stopped"


class SessionState(str, Enum):
    IDLE = "idle"
    PREFLIGHTING = "preflighting"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LiveRuntimeMetrics:
    elapsed_seconds: float
    source_states: dict[str, SourceState]
    source_stats: dict[str, Any]
    scheduler: Any
    drift: Any
    asr: Any
    profile: Any


class LiveRuntime(QObject):
    """Own live sources and one persistent ASR worker without UI policy."""

    segment_update = pyqtSignal(object)
    source_state_changed = pyqtSignal(str, str)
    level_changed = pyqtSignal(str, float)
    error_occurred = pyqtSignal(str, str)
    finished = pyqtSignal(object, str)
    session_state_changed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: PersistentWhisperWorker | None = None
        self._pipeline: LiveSessionPipeline | None = None
        self._sources: dict[str, Any] = {}
        self._states: dict[str, SourceState] = {}
        self._drainers: list[threading.Thread] = []
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._pipeline_lock = threading.Lock()
        self._started_at = 0.0
        self._language = "auto"
        self._output_path = ""
        self._session_state = SessionState.IDLE
        self._worker_ready = False
        self._profile = resource_profile()

    @property
    def session_state(self) -> SessionState:
        return self._session_state

    def start(
        self,
        *,
        use_mic: bool,
        use_system: bool,
        model_name: str,
        language: str = "auto",
        mic_device: int | None = None,
        target: CaptureTarget | None = None,
        helper_path: Path | None = None,
    ) -> bool:
        if self.is_running():
            return True
        source_ids = tuple(
            source for source, enabled in (("mic", use_mic), ("system", use_system)) if enabled
        )
        if not source_ids:
            self.error_occurred.emit("session", "Select at least one audio source")
            return False
        self._stop.clear()
        self._paused.clear()
        self._sources.clear()
        self._states = {source: SourceState.STARTING for source in source_ids}
        self._set_session_state(SessionState.STARTING)
        self._worker_ready = False
        self._language = language
        self._started_at = time.monotonic()
        profile = resource_profile()
        self._profile = profile
        self._worker = PersistentWhisperWorker(model_name, language=language, parent=self)
        self._worker.ready.connect(self._on_worker_ready)
        self._worker.error.connect(lambda message: self._source_error("asr", message))
        self._pipeline = LiveSessionPipeline(
            self._worker,
            sources=source_ids,
            partial_interval_seconds=profile.partial_interval_seconds,
            update_callback=self.segment_update.emit,
        )
        self._pipeline.scheduler.max_pending_per_source = profile.max_pending_per_source
        self._worker.start()

        if use_mic:
            mic = MicSource(self, enabled=True, device=mic_device)
            mic.level_changed.connect(lambda value: self.level_changed.emit("mic", value))
            mic.error_occurred.connect(lambda message: self._source_error("mic", message))
            self._sources["mic"] = mic
            if mic.start():
                self._output_path = mic.recorder._output_path or ""
                self._set_state("mic", SourceState.RUNNING)
                self._start_drain("mic", mic)
            else:
                self._set_state("mic", SourceState.FAILED)

        if use_system:
            helper = helper_path or default_helper_path()
            socket_dir = data_dir() / "runtime"
            socket_dir.mkdir(parents=True, exist_ok=True)
            socket_path = socket_dir / f"capture-{os.getpid()}-{int(time.time())}.sock"
            system = SystemAudioSource(
                self,
                socket_path=str(socket_path),
                enabled=True,
                helper_command=(str(helper),),
            )
            system.level_changed.connect(lambda value: self.level_changed.emit("system", value))
            system.error_occurred.connect(lambda message: self._source_error("system", message))
            system.started.connect(lambda: self._set_state("system", SourceState.RUNNING))
            system.stopped.connect(lambda: self._set_state("system", SourceState.STOPPED))
            self._sources["system"] = system
            if system.start(target or CaptureTarget(bundle_id="us.zoom.xos")):
                self._start_drain("system", system)
            else:
                self._set_state("system", SourceState.FAILED)
        started = any(state is not SourceState.FAILED for state in self._states.values())
        if not started:
            self._set_session_state(SessionState.FAILED)
        return started

    def pause(self) -> None:
        self._paused.set()
        mic = self._sources.get("mic")
        if mic is not None and mic.is_recording():
            mic.pause()
        for source in self._states:
            if self._states[source] is SourceState.RUNNING:
                self._set_state(source, SourceState.PAUSED)
        self._set_session_state(SessionState.PAUSED)

    def resume(self) -> None:
        mic = self._sources.get("mic")
        if mic is not None and mic.is_recording():
            mic.resume()
        self._paused.clear()
        for source in self._states:
            if self._states[source] is SourceState.PAUSED:
                self._set_state(source, SourceState.RUNNING)
        self._set_session_state(SessionState.RUNNING)

    def stop(self) -> None:
        if not self.is_running():
            return
        self._set_session_state(SessionState.FINALIZING)
        threading.Thread(target=self._finish_session, daemon=True).start()

    def cancel(self) -> None:
        self._stop.set()
        for source in self._sources.values():
            source.cancel()
        if self._worker is not None:
            self._worker.cancel()

    def is_running(self) -> bool:
        return bool(self._states) and any(
            state in {SourceState.STARTING, SourceState.RUNNING, SourceState.PAUSED}
            for state in self._states.values()
        )

    def metrics(self) -> LiveRuntimeMetrics:
        pipeline = self._pipeline
        return LiveRuntimeMetrics(
            elapsed_seconds=max(0.0, time.monotonic() - self._started_at) if self._started_at else 0.0,
            source_states=dict(self._states),
            source_stats={source: adapter.stats() for source, adapter in self._sources.items()},
            scheduler=pipeline.scheduler.stats() if pipeline else None,
            drift=pipeline.drift_report() if pipeline else None,
            asr=self._worker.metrics() if self._worker else None,
            profile=self._profile,
        )

    def _start_drain(self, source: str, adapter: Any) -> None:
        thread = threading.Thread(
            target=self._drain,
            args=(source, adapter),
            name=f"live-{source}-drain",
            daemon=True,
        )
        self._drainers.append(thread)
        thread.start()

    def _drain(self, source: str, adapter: Any) -> None:
        discontinuity = False
        while not self._stop.is_set():
            frame = adapter.get_frame(timeout=0.1)
            if frame is None:
                if adapter.stats().closed:
                    return
                continue
            if self._paused.is_set():
                discontinuity = True
                continue
            if discontinuity:
                frame = replace(frame, discontinuity=True)
                discontinuity = False
            try:
                with self._pipeline_lock:
                    if self._pipeline is not None:
                        self._pipeline.feed(frame)
            except Exception as exc:
                self._source_error(source, str(exc))
                return

    def _finish_session(self) -> None:
        for source, adapter in tuple(self._sources.items()):
            try:
                path = adapter.stop()
                if source == "mic" and path:
                    self._output_path = path
            except Exception as exc:
                self._source_error(source, str(exc))
        self._stop.set()
        for thread in self._drainers:
            thread.join(timeout=1.0)
        with self._pipeline_lock:
            if self._pipeline is not None:
                self._pipeline.flush()
        deadline = time.monotonic() + 10.0
        while self._pipeline is not None and time.monotonic() < deadline:
            stats = self._pipeline.scheduler.stats()
            if stats.queued == 0 and stats.in_flight == 0:
                break
            time.sleep(0.05)
        if self._worker is not None:
            self._worker.shutdown()
            self._worker.wait(3000)
        result = self._pipeline.build_result(self._language) if self._pipeline else None
        for source in self._states:
            self._set_state(source, SourceState.STOPPED)
        if result is not None:
            self._set_session_state(SessionState.COMPLETED)
            self.finished.emit(result, self._output_path)

    def _source_error(self, source: str, message: str) -> None:
        if source in self._states:
            self._set_state(source, SourceState.FAILED)
        self.error_occurred.emit(source, message)
        if source in {"asr", "session"}:
            self._set_session_state(SessionState.FAILED)
            self.cancel()
        elif self._states and all(
            state in {SourceState.FAILED, SourceState.STOPPED}
            for state in self._states.values()
        ):
            self._set_session_state(SessionState.FAILED)

    def _set_state(self, source: str, state: SourceState) -> None:
        self._states[source] = state
        self.source_state_changed.emit(source, state.value)
        if state is SourceState.RUNNING:
            self._maybe_mark_running()

    def _on_worker_ready(self, _load_seconds: float) -> None:
        self._worker_ready = True
        self._maybe_mark_running()

    def _maybe_mark_running(self) -> None:
        if self._worker_ready and any(
            state is SourceState.RUNNING for state in self._states.values()
        ):
            self._set_session_state(SessionState.RUNNING)

    def _set_session_state(self, state: SessionState) -> None:
        if state is self._session_state:
            return
        self._session_state = state
        self.session_state_changed.emit(state.value)
