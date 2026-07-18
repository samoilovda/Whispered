"""Python-side system-audio source for the L8 capture-helper protocol."""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
import math

from PyQt6.QtCore import QObject, pyqtSignal

from config import get_config
from core.live.audio_buffer import BoundedAudioRing, MonotonicTimestamp, RingStats
from core.live.contracts import AudioFrame
from core.live.system_capture_protocol import (
    CaptureLifecycle,
    CaptureTarget,
    FrameDecoder,
    IPCFrame,
    MessageType,
    ProtocolError,
    start_frame,
    stop_frame,
)
from core.logger import get_logger

logger = get_logger(__name__)


class SystemAudioSource(QObject):
    """Receive system PCM from a separately launched ScreenCaptureKit helper."""

    level_changed = pyqtSignal(float)
    error_occurred = pyqtSignal(str)
    started = pyqtSignal()
    stopped = pyqtSignal()

    def __init__(
        self,
        parent=None,
        *,
        socket_path: str,
        enabled: bool | None = None,
        helper_command: Sequence[str] | None = None,
        socket_factory: Callable[[str], object] | None = None,
        capacity_frames: int = 100,
        capacity_bytes: int | None = None,
    ) -> None:
        super().__init__(parent)
        if not socket_path:
            raise ValueError("socket_path must not be empty")
        self.enabled = (
            get_config().live_transcription_enabled if enabled is None else enabled
        )
        self.socket_path = socket_path
        self.helper_command = tuple(helper_command or ())
        self._socket_factory = socket_factory or _connect_unix_socket
        self._capacity_frames = capacity_frames
        self._capacity_bytes = capacity_bytes
        self._ring = self._new_ring()
        self._ring.close()
        self._clock = MonotonicTimestamp()
        self._lifecycle = CaptureLifecycle()
        self._target: CaptureTarget | None = None
        self._socket = None
        self._helper_process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._stopped_event = threading.Event()
        self._stopped_emitted = False
        self._sequence_fallback = 0
        self._lock = threading.Lock()

    def start(self, target: CaptureTarget) -> bool:
        """Start helper connection asynchronously; ``started`` confirms readiness."""
        if not self.enabled:
            self.error_occurred.emit(
                "Live transcription is disabled in Settings; enable it before starting."
            )
            return False
        if self.is_running():
            return True
        self._target = target
        self._ring = self._new_ring()
        self._clock = MonotonicTimestamp()
        self._lifecycle = CaptureLifecycle()
        self._sequence_fallback = 0
        self._stop_event.clear()
        self._stopped_event.clear()
        self._stopped_emitted = False
        self._launch_helper()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        return True

    def stop(self) -> None:
        """Request helper Stop and wait at most two seconds for socket cleanup."""
        deadline = time.monotonic() + 2.0
        with self._lock:
            sock = self._socket
            lifecycle = self._lifecycle
        if sock is not None:
            try:
                lifecycle.prepare_stop()
                sock.sendall(stop_frame())
            except (OSError, ProtocolError):
                pass
        # Keep the reader alive long enough to consume the helper's STOPPED
        # acknowledgement and any final control frame.
        if sock is not None:
            self._stopped_event.wait(timeout=max(0.0, deadline - time.monotonic()))
        self._stop_event.set()
        if self._reader is not None:
            self._reader.join(timeout=max(0.0, deadline - time.monotonic()))
        self._close_socket()
        self._stop_helper(timeout=max(0.0, deadline - time.monotonic()))
        self._ring.close()
        self._emit_stopped_once()

    def cancel(self) -> None:
        """Abort socket/helper immediately and discard pending system audio."""
        self._stop_event.set()
        self._ring.cancel()
        self._close_socket()
        self._stop_helper(force=True)

    def is_running(self) -> bool:
        return self._lifecycle.state in {"starting", "running", "stopping"}

    def get_frame(self, timeout: float | None = None) -> AudioFrame | None:
        return self._ring.get(timeout=timeout)

    def stats(self) -> RingStats:
        return self._ring.stats()

    @property
    def lifecycle_state(self) -> str:
        return self._lifecycle.state

    def _launch_helper(self) -> None:
        if not self.helper_command:
            return
        env = os.environ.copy()
        env["WHISPERED_CAPTURE_SOCKET"] = self.socket_path
        try:
            self._helper_process = subprocess.Popen(self.helper_command, env=env)
        except OSError as exc:
            self.error_occurred.emit(f"Cannot launch system capture helper: {exc}")

    def _read_loop(self) -> None:
        try:
            sock = self._connect_with_retry()
            if sock is None:
                return
            with self._lock:
                self._socket = sock
            decoder = FrameDecoder()
            while not self._stop_event.is_set():
                try:
                    data = sock.recv(64 * 1024)
                except (socket.timeout, TimeoutError):
                    continue
                if not data:
                    raise ConnectionError("system capture helper closed the socket")
                for frame in decoder.feed(data):
                    self._consume(frame, sock)
                    if self._stop_event.is_set():
                        break
        except (OSError, ConnectionError, ProtocolError) as exc:
            if not self._stop_event.is_set():
                self.error_occurred.emit(_friendly_capture_error(exc))
        finally:
            self._close_socket()
            self._ring.close()

    def _connect_with_retry(self):
        deadline = time.monotonic() + 2.0
        while not self._stop_event.is_set():
            try:
                sock = self._socket_factory(self.socket_path)
                if hasattr(sock, "settimeout"):
                    sock.settimeout(0.25)
                if hasattr(sock, "connect"):
                    sock.connect(self.socket_path)
                return sock
            except (FileNotFoundError, ConnectionRefusedError, TimeoutError, OSError) as exc:
                try:
                    sock.close()
                except (UnboundLocalError, AttributeError, OSError):
                    pass
                if time.monotonic() >= deadline:
                    raise ConnectionError(f"cannot connect to capture helper: {exc}") from exc
                time.sleep(0.05)
        return None

    def _consume(self, frame: IPCFrame, sock) -> None:
        self._lifecycle.consume(frame)
        if frame.message_type is MessageType.HELLO:
            if self._target is None:
                raise ProtocolError("capture target is missing")
            self._lifecycle.prepare_start()
            sock.sendall(start_frame(self._target))
        elif frame.message_type is MessageType.STARTED:
            self.started.emit()
        elif frame.message_type is MessageType.AUDIO:
            self._consume_audio(frame)
        elif frame.message_type is MessageType.METER:
            self.level_changed.emit(float(frame.header.get("rms", 0.0)))
        elif frame.message_type is MessageType.ERROR:
            self.error_occurred.emit(_friendly_capture_error_from_frame(frame))
        elif frame.message_type is MessageType.STOPPED:
            self._stopped_event.set()
            self._stop_event.set()
            self._emit_stopped_once()

    def _consume_audio(self, frame: IPCFrame) -> None:
        header = frame.header
        sample_rate = int(header["sample_rate"])
        channels = int(header["channels"])
        if channels != 1 or header.get("sample_format") != "s16le":
            raise ProtocolError("system audio helper must send mono s16le PCM")
        sequence = int(header.get("sequence", self._sequence_fallback))
        self._sequence_fallback = sequence + 1
        monotonic_timestamp = self._clock.now(float(header["monotonic_timestamp"]))
        source_timestamp = float(header["source_timestamp"])
        if not math.isfinite(source_timestamp) or source_timestamp < 0:
            raise ProtocolError("system audio source_timestamp is invalid")
        audio = AudioFrame(
            source="system",
            sequence=sequence,
            source_timestamp=source_timestamp,
            monotonic_timestamp=monotonic_timestamp,
            sample_rate=sample_rate,
            pcm=frame.payload,
        )
        self._ring.put(audio)

    def _close_socket(self) -> None:
        with self._lock:
            sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _stop_helper(self, force: bool = False, timeout: float = 1.0) -> None:
        process, self._helper_process = self._helper_process, None
        if process is None or process.poll() is not None:
            return
        if force:
            process.kill()
        else:
            process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                logger.warning("System capture helper did not exit after SIGKILL")

    def _new_ring(self) -> BoundedAudioRing:
        return BoundedAudioRing(self._capacity_frames, self._capacity_bytes)

    def _emit_stopped_once(self) -> None:
        with self._lock:
            if self._stopped_emitted:
                return
            self._stopped_emitted = True
        self.stopped.emit()


def _connect_unix_socket(path: str):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    return _ConnectingSocket(sock, path)


class _ConnectingSocket:
    """Connect-on-construction wrapper used by the retry loop."""

    def __init__(self, sock, path: str) -> None:
        self._sock = sock
        self._path = path

    def connect(self, _path: str) -> None:
        self._sock.connect(self._path)

    def settimeout(self, value: float) -> None:
        self._sock.settimeout(value)

    def recv(self, size: int) -> bytes:
        return self._sock.recv(size)

    def sendall(self, data: bytes) -> None:
        self._sock.sendall(data)

    def close(self) -> None:
        self._sock.close()


def _friendly_capture_error(exc: Exception) -> str:
    return f"System audio capture unavailable: {exc}"


def _friendly_capture_error_from_frame(frame: IPCFrame) -> str:
    code = frame.header.get("code")
    message = frame.header.get("message", "System audio capture failed")
    if code in {"screen_recording_denied", "permission_denied"}:
        return (
            "macOS Screen Recording permission is required for system audio. "
            "Enable Whispered in System Settings → Privacy & Security → Screen Recording, then retry."
        )
    return str(message)
