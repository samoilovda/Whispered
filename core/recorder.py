"""
Whispered – Microphone Recorder
Records audio from a selected input device to a 16 kHz mono WAV file.

Usage (within a QThread or main thread):
    rec = Recorder()
    rec.level_changed.connect(lambda rms: update_meter(rms))
    rec.error_occurred.connect(handle_error)
    rec.start(device_index=None)   # None = default device
    ...
    path = rec.stop()              # returns path to WAV file, or None on failure

Invariants
----------
- Bounded queue: audio callback never blocks. Overflow drops newest frames
  and increments ``_dropped_frames`` (reported to callers via ``dropped_frames``
  property).
- Single terminal outcome: exactly one of "success" (valid WAV file returned
  by stop()) or "failure" (error_occurred emitted, None returned by stop()).
- Atomic write: data goes to ``<target>.part`` in the same directory;
  on clean finish → ``os.fsync`` + ``os.replace``.  On failure/cancel the
  ``.part`` file is removed.
"""

from __future__ import annotations

import queue
import math
import os
import struct
import threading
import wave
from datetime import datetime
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import get_logger
from core.paths import data_dir

logger = get_logger(__name__)

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    np = None  # type: ignore[assignment]
    _NUMPY_AVAILABLE = False

_SAMPLE_RATE = 16_000      # Whisper's native sample rate
_CHANNELS = 1              # Mono
_DTYPE = "int16"           # 16-bit PCM
_BLOCKSIZE = 1_600         # 100 ms chunks → level at 10 Hz
_DATA_DIR = data_dir() / "recordings"

# Bounded queue capacity: ~5 seconds of 16 kHz mono int16 audio.
# Each chunk is _BLOCKSIZE samples × 2 bytes = 3200 bytes; 5 s = 50 chunks.
# Formula: (5 * _SAMPLE_RATE / _BLOCKSIZE) = 5 * 16000 / 1600 = 50.
_QUEUE_MAXSIZE: int = 50


def _rms(block) -> float:
    """Root-mean-square of a numpy int16 block, normalised to [0, 1]."""
    arr = block.astype("float32") / 32768.0
    return float(np.sqrt(np.mean(arr ** 2)))


def _rms_pcm16(data: bytes) -> float:
    """Compute normalised RMS for little-endian PCM16 without NumPy."""
    sample_bytes = data[:len(data) - (len(data) % 2)]
    if not sample_bytes:
        return 0.0
    samples = struct.iter_unpack("<h", sample_bytes)
    total = 0.0
    count = 0
    for (sample,) in samples:
        normalized = sample / 32768.0
        total += normalized * normalized
        count += 1
    return math.sqrt(total / count) if count else 0.0


def list_input_devices() -> list[dict]:
    """Return a list of available input devices.

    Each item: {"index": int, "name": str, "channels": int}
    Returns [] if sounddevice is not available.
    """
    try:
        import sounddevice as sd
        devices = []
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                devices.append(
                    {"index": i, "name": dev["name"], "channels": dev["max_input_channels"]}
                )
        return devices
    except Exception as exc:
        logger.warning("Cannot query audio devices: %s", exc)
        return []


class Recorder(QObject):
    """Thread-safe microphone recorder.

    Signals
    -------
    level_changed(float)  — RMS level in [0, 1], emitted at ~10 Hz
    error_occurred(str)   — error message (exactly once per failed recording)
    """

    level_changed = pyqtSignal(float)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        parent=None,
        *,
        frame_sink: Optional[Callable] = None,
        write_wav: bool = True,
    ):
        super().__init__(parent)
        # sounddevice.InputStream — untyped here because sounddevice is
        # imported lazily (see _open_stream) so the app can start without it.
        self._stream: Optional[Any] = None
        self._wav: Optional[wave.Wave_write] = None
        self._output_path: Optional[str] = None
        self._part_path: Optional[str] = None
        self._lock = threading.Lock()
        self._recording = threading.Event()
        self._paused = threading.Event()
        # Bounded queue: overflow drops newest frames (callback never blocks)
        self._q: queue.Queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._writer_thread: Optional[threading.Thread] = None
        self._elapsed_frames: int = 0
        # Overflow / drop tracking
        self._dropped_frames: int = 0
        # Fatal writer state: set by _writer() on I/O exception
        self._writer_fatal: threading.Event = threading.Event()
        self._writer_error_msg: str = ""
        self._error_emitted: threading.Event = threading.Event()
        # Optional live adapter hook. It must be non-blocking: this is called
        # from PortAudio's callback thread and is deliberately absent for the
        # legacy recorder path.
        self._frame_sink = frame_sink
        # Live transcription needs microphone PCM but must not silently turn
        # every meeting into a recording.  Keep the legacy default intact and
        # allow its adapter to opt out of all file/queue writer work.
        self._write_wav = bool(write_wav)

    # ------------------------------------------------------------------ public

    @property
    def dropped_frames(self) -> int:
        """Number of audio frames dropped due to queue overflow since last start."""
        return self._dropped_frames

    def start(self, device: Optional[int] = None) -> None:
        """Begin recording from *device* (None = system default)."""
        try:
            import sounddevice as sd
        except ImportError:
            self.error_occurred.emit(
                "sounddevice is not installed.\n\nInstall it with:\n  pip install sounddevice numpy"
            )
            return

        self._elapsed_frames = 0
        self._dropped_frames = 0
        self._writer_fatal.clear()
        self._error_emitted.clear()
        self._writer_error_msg = ""
        self._output_path = None
        self._part_path = None

        if self._write_wav:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            # Microseconds avoid overwriting a just-finished recording when
            # the user starts another one within the same second.
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
            self._output_path = str(_DATA_DIR / f"REC_{ts}.wav")
            self._part_path = self._output_path + ".part"
            try:
                self._wav = wave.open(self._part_path, "wb")
                if os.name != "nt":
                    os.chmod(self._part_path, 0o600)
                self._wav.setnchannels(_CHANNELS)
                self._wav.setsampwidth(2)    # int16 → 2 bytes
                self._wav.setframerate(_SAMPLE_RATE)
            except OSError as exc:
                self.error_occurred.emit(f"Cannot create recording file: {exc}")
                return

        self._recording.set()
        self._paused.clear()
        # Fresh bounded queue — discard any stale sentinel from a previous stop()
        self._q = queue.Queue(maxsize=_QUEUE_MAXSIZE)

        # Open the audio stream BEFORE starting the writer thread so that a
        # failed InputStream() doesn't leave an unkillable thread behind.
        try:
            self._stream = sd.InputStream(
                device=device,
                samplerate=_SAMPLE_RATE,
                channels=_CHANNELS,
                dtype=_DTYPE,
                blocksize=_BLOCKSIZE,
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as exc:
            self._recording.clear()
            self._cleanup_part()
            self._emit_error(f"Cannot open audio device: {exc}")
            return

        # Writer thread is deliberately absent for transcript-only Live
        # sessions: their PCM remains only in bounded in-memory buffers.
        if self._write_wav:
            self._writer_thread = threading.Thread(target=self._writer, daemon=True)
            self._writer_thread.start()
            logger.info("Recording started: %s", self._output_path)
        else:
            logger.info("Microphone capture started without WAV persistence")

    def pause(self) -> None:
        """Pause recording (frames are discarded while paused)."""
        self._paused.set()

    def resume(self) -> None:
        """Resume after pause."""
        self._paused.clear()

    def stop(self) -> Optional[str]:
        """Stop recording and return the path to the WAV file, or None on failure.

        A returned path is guaranteed to be a complete, valid WAV file that
        was atomically replaced from the ``.part`` staging file.

        Returns ``None`` if:
        - recording was never started;
        - the writer thread hit a fatal I/O error;
        - the writer thread did not drain within the 5 s timeout.
        """
        # Error paths may call stop() after stream initialisation failed.
        if not self._recording.is_set() and self._stream is None and self._wav is None:
            return None
        self._recording.clear()

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.debug("Audio stream close error: %s", exc)
            self._stream = None

        # Signal writer thread to finish when this is a persistent recording.
        drain_ok = True
        if self._writer_thread is not None:
            self._q.put(None)  # sentinel
            self._writer_thread.join(timeout=5)
            if self._writer_thread.is_alive():
                logger.error("Recorder: writer thread did not stop within 5 s")
                drain_ok = False
            self._writer_thread = None

        # Close WAV before deciding on success
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None

        # Determine outcome
        writer_ok = not self._writer_fatal.is_set()
        success = drain_ok and writer_ok

        if success and self._part_path and self._output_path:
            try:
                # fsync the .part file via re-opening in binary mode
                with open(self._part_path, "r+b") as fh:
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(self._part_path, self._output_path)
            except OSError as exc:
                logger.error("Recorder: atomic replace failed: %s", exc)
                self._cleanup_part()
                self._emit_error(f"Recording save failed: {exc}")
                return None
            path = self._output_path
            logger.info("Recording stopped: %s (%d frames)", path, self._elapsed_frames)
            if self._dropped_frames:
                logger.warning(
                    "Recorder: %d audio frames were dropped due to buffer overflow",
                    self._dropped_frames,
                )
            return path

        # Failure path: remove the .part file and emit error
        self._cleanup_part()
        if not writer_ok and not self._error_emitted.is_set():
            self._emit_error(
                self._writer_error_msg or "Recording failed: WAV writer error"
            )
        elif not drain_ok and not self._error_emitted.is_set():
            self._emit_error("Recording failed: writer thread did not drain in time")
        return None

    def is_recording(self) -> bool:
        return self._recording.is_set()

    @property
    def elapsed_seconds(self) -> float:
        return self._elapsed_frames / _SAMPLE_RATE

    # ------------------------------------------------------------------ internal

    def _emit_error(self, msg: str) -> None:
        """Emit error_occurred exactly once per recording session."""
        if not self._error_emitted.is_set():
            self._error_emitted.set()
            self.error_occurred.emit(msg)

    def _cleanup_part(self) -> None:
        """Remove the .part staging file if it exists."""
        if self._part_path:
            try:
                if os.path.exists(self._part_path):
                    os.unlink(self._part_path)
            except OSError as exc:
                logger.debug("Recorder: could not remove .part file: %s", exc)
            self._part_path = None

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            logger.debug("Audio stream status: %s", status)
        if not self._paused.is_set():
            try:
                chunk = bytes(indata)
                if self._write_wav:
                    try:
                        self._q.put_nowait(chunk)
                    except queue.Full:
                        # Drop newest: callback must not block
                        self._dropped_frames += frames
                else:
                    self._elapsed_frames += frames
                if _NUMPY_AVAILABLE:
                    self.level_changed.emit(_rms(indata))
                else:
                    self.level_changed.emit(_rms_pcm16(chunk))
                if self._frame_sink is not None:
                    try:
                        self._frame_sink(chunk, frames, time_info)
                    except Exception as exc:
                        logger.debug("Live frame sink error: %s", exc)
            except Exception as exc:
                logger.debug("Audio callback error: %s", exc)

    def _writer(self):
        """Background thread: drains queue and writes to WAV file.

        Exits cleanly on sentinel (None).  On I/O exception sets
        ``_writer_fatal`` and exits; the error is surfaced in ``stop()``.
        """
        while True:
            chunk = self._q.get()
            if chunk is None:
                break
            if self._wav is not None:
                try:
                    self._wav.writeframes(chunk)
                    self._elapsed_frames += len(chunk) // 2  # int16 → 2 bytes/sample
                except Exception as exc:
                    logger.error("WAV write error: %s", exc)
                    self._writer_error_msg = str(exc)
                    self._writer_fatal.set()
                    # Drain remaining items so stop() can join quickly
                    try:
                        while True:
                            self._q.get_nowait()
                    except queue.Empty:
                        pass
                    break
