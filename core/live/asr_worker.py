"""Persistent whisper.cpp worker for live microphone turns."""

from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from core.live.contracts import SpeechTurn
from core.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LiveASRResult:
    """One decoded VAD turn and its final batch-compatible segments."""

    request_id: int
    turn: SpeechTurn
    segments: tuple[Any, ...]
    latency_seconds: float
    final: bool = True


@dataclass(frozen=True, slots=True)
class LiveASRMetrics:
    """Snapshot of persistent worker health and final decode latency."""

    submitted: int
    completed: int
    failed: int
    cancelled: bool
    model_load_seconds: float | None
    final_latency_p95: float | None


class _LiveDecodeEngine:
    """Decode raw PCM with one already-loaded pywhispercpp model."""

    def __init__(
        self,
        model: Any,
        language: str,
        n_threads: int,
        abort_event,
    ) -> None:
        self.model = model
        self.language = language
        self.n_threads = n_threads
        self.abort_event = abort_event
        self._resolved_language: str | None = None

    def decode(
        self, request_id: int, turn: SpeechTurn, pcm: bytes, final: bool = True
    ) -> dict:
        audio = _pcm_to_float32(pcm)
        if audio.size == 0:
            return {
                "request_id": request_id,
                "turn": turn,
                "segments": (),
                "decode_seconds": 0.0,
                "final": final,
            }

        language = self._language_for(audio)
        started = time.monotonic()
        raw_segments = self.model.transcribe(
            audio,
            n_threads=self.n_threads,
            language=language,
            no_context=True,
            no_speech_thold=0.6,
            print_progress=False,
            print_realtime=False,
            abort_callback=self.abort_event.is_set,
        )
        segments = tuple(
            {
                "start": turn.start + float(segment.t0) / 100.0,
                "end": turn.start + float(segment.t1) / 100.0,
                "text": str(segment.text),
                "speaker": turn.source,
            }
            for segment in raw_segments
            if str(segment.text).strip()
        )
        return {
            "request_id": request_id,
            "turn": turn,
            "segments": segments,
            "decode_seconds": time.monotonic() - started,
            "final": final,
        }

    def _language_for(self, audio) -> str:
        if self.language != "auto":
            return self.language
        if self._resolved_language:
            return self._resolved_language
        try:
            (detected, confidence), _ = self.model.auto_detect_language(
                audio, n_threads=self.n_threads
            )
            self._resolved_language = str(detected)
            logger.info("Live auto-detected language: %s (p=%.3f)", detected, confidence)
        except Exception as exc:
            logger.warning("Live language auto-detection failed: %s", exc)
            self._resolved_language = "en"
        return self._resolved_language


def _run_live_whisper_process(
    commands,
    results,
    model_name: str,
    language: str,
    n_threads: int,
    abort_event,
) -> None:
    """Spawn target: load one model, then serve turns until shutdown."""
    try:
        from utils import get_models_dir
        from pywhispercpp.model import Model

        started = time.monotonic()
        model = Model(
            model_name,
            models_dir=get_models_dir(),
            print_progress=False,
            print_realtime=False,
        )
        results.put(("ready", time.monotonic() - started))
        engine = _LiveDecodeEngine(model, language, n_threads, abort_event)
        _serve_live_commands(commands, results, engine, abort_event)
    except MemoryError:
        results.put(("error", f"Not enough memory to load model '{model_name}'."))
    except Exception as exc:
        results.put(("error", f"Failed to start live whisper worker: {exc}"))
    finally:
        if not abort_event.is_set():
            results.put(("stopped",))


def _serve_live_commands(commands, results, engine: _LiveDecodeEngine, abort_event) -> None:
    """Serve commands with a cancellation check between and during decodes."""
    while not abort_event.is_set():
        try:
            command = commands.get(timeout=0.1)
        except queue.Empty:
            continue

        kind = command[0]
        if kind == "shutdown":
            return
        if kind != "transcribe":
            results.put(("error", f"Unknown live ASR command: {kind}"))
            continue

        if len(command) == 4:  # v1 compatibility for tests/older callers
            _, request_id, turn, pcm = command
            final = True
        else:
            _, request_id, turn, pcm, final = command
        try:
            result = engine.decode(request_id, turn, pcm, final=bool(final))
            results.put(("final" if final else "decoded", result))
        except Exception as exc:
            if abort_event.is_set():
                return
            results.put(("decode_error", request_id, str(exc)))


class PersistentWhisperWorker(QThread):
    """Qt-facing parent worker around one cancellable whisper process."""

    ready = pyqtSignal(float)
    partial = pyqtSignal(object)
    final = pyqtSignal(object)
    decode_error = pyqtSignal(str)
    decode_error_detail = pyqtSignal(object)
    error = pyqtSignal(str)
    latency = pyqtSignal(float)

    def __init__(
        self,
        model_name: str,
        language: str = "auto",
        n_threads: int = 4,
        queue_size: int = 8,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if not model_name:
            raise ValueError("model_name must not be empty")
        if n_threads < 1:
            raise ValueError("n_threads must be positive")
        if queue_size < 1:
            raise ValueError("queue_size must be positive")

        self.model_name = model_name
        self.language = language
        self.n_threads = n_threads
        self._ctx = mp.get_context("spawn")
        self._commands = self._ctx.Queue(maxsize=queue_size)
        self._results = self._ctx.Queue()
        self._abort_event = self._ctx.Event()
        self._cancelled = threading.Event()
        self._shutdown_requested = threading.Event()
        self._process: Any = None
        self._next_request_id = 1
        self._pending: dict[int, float] = {}
        self._latencies: list[float] = []
        self._submitted = 0
        self._completed = 0
        self._failed = 0
        self._model_load_seconds: float | None = None
        self._metrics_lock = threading.Lock()

    def submit(self, turn: SpeechTurn, pcm: bytes, *, final: bool = True) -> int | None:
        """Queue one VAD turn without waiting; return request id or ``None``."""
        if self._cancelled.is_set() or self._shutdown_requested.is_set():
            return None
        if not isinstance(turn, SpeechTurn):
            raise TypeError("turn must be a SpeechTurn")
        request_id = self._next_request_id
        try:
            self._commands.put_nowait(
                ("transcribe", request_id, turn, bytes(pcm), bool(final))
            )
        except queue.Full:
            return None
        self._next_request_id += 1
        with self._metrics_lock:
            self._pending[request_id] = time.monotonic()
            self._submitted += 1
        return request_id

    def cancel(self) -> None:
        """Request abort; the child callback and hard-stop fallback share one flag."""
        self._cancelled.set()
        self._abort_event.set()

    def shutdown(self) -> None:
        """Ask the child to drain queued turns and exit normally."""
        if self._cancelled.is_set():
            return
        self._shutdown_requested.set()
        try:
            self._commands.put_nowait(("shutdown",))
        except queue.Full:
            # Cancellation remains the bounded fallback if ASR is overloaded.
            self.cancel()

    def metrics(self) -> LiveASRMetrics:
        with self._metrics_lock:
            return LiveASRMetrics(
                submitted=self._submitted,
                completed=self._completed,
                failed=self._failed,
                cancelled=self._cancelled.is_set(),
                model_load_seconds=self._model_load_seconds,
                final_latency_p95=_percentile(self._latencies, 0.95),
            )

    def run(self) -> None:
        if self._cancelled.is_set():
            return
        self._process = self._ctx.Process(
            target=_run_live_whisper_process,
            args=(
                self._commands,
                self._results,
                self.model_name,
                self.language,
                self.n_threads,
                self._abort_event,
            ),
        )
        self._process.start()
        child_stopped = False
        try:
            while self._process.is_alive() or not child_stopped:
                if self._cancelled.is_set():
                    self._stop_child()
                    return
                try:
                    message = self._results.get(timeout=0.1)
                except queue.Empty:
                    if not self._process.is_alive():
                        break
                    continue
                if message[0] == "stopped":
                    child_stopped = True
                    continue
                self._handle_message(message)
            if not self._cancelled.is_set() and self._process.exitcode not in (0, None):
                self.error.emit(
                    f"Live whisper worker crashed with exit code {self._process.exitcode}"
                )
        finally:
            if self._process and self._process.is_alive():
                self._stop_child()
            _close_queue(self._commands)
            _close_queue(self._results)

    def _handle_message(self, message: tuple) -> None:
        kind = message[0]
        if kind == "ready":
            self._model_load_seconds = float(message[1])
            self.ready.emit(self._model_load_seconds)
            return
        if kind == "error":
            self._failed += 1
            self.error.emit(str(message[1]))
            return
        if kind == "decode_error":
            _, request_id, error = message
            with self._metrics_lock:
                self._pending.pop(request_id, None)
                self._failed += 1
            self.decode_error_detail.emit((request_id, str(error)))
            self.decode_error.emit(str(error))
            return
        if kind not in {"final", "decoded"}:
            self.error.emit(f"Unknown live whisper result: {kind}")
            return

        payload = message[1]
        request_id = int(payload["request_id"])
        with self._metrics_lock:
            submitted_at = self._pending.pop(request_id, time.monotonic())
            end_to_end = max(0.0, time.monotonic() - submitted_at)
            self._latencies.append(end_to_end)
            self._completed += 1
        result = LiveASRResult(
            request_id=request_id,
            turn=payload["turn"],
            segments=tuple(_to_batch_segments(payload["segments"])),
            latency_seconds=end_to_end,
            final=bool(payload.get("final", True)),
        )
        self.latency.emit(end_to_end)
        if result.final:
            self.final.emit(result)
        else:
            self.partial.emit(result)

    def _stop_child(self) -> None:
        self._abort_event.set()
        if not self._process or not self._process.is_alive():
            return
        self._process.terminate()
        self._process.join(timeout=1.5)
        if self._process.is_alive():
            logger.warning("Live whisper worker still alive after SIGTERM; sending SIGKILL")
            self._process.kill()
            self._process.join(timeout=0.5)


def _to_batch_segments(payload: tuple[dict, ...]) -> list[Any]:
    """Convert process-safe dictionaries to the existing Segment model."""
    from transcriber import Segment

    return [
        Segment(
            start=float(item["start"]),
            end=float(item["end"]),
            text=str(item["text"]),
            speaker=item.get("speaker"),
        )
        for item in payload
    ]


def _pcm_to_float32(pcm: bytes):
    import numpy as np

    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _close_queue(q) -> None:
    try:
        q.close()
        q.join_thread()
    except (AttributeError, OSError):
        pass


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]
