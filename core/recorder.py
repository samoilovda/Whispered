"""
Whispered – Microphone Recorder
Records audio from a selected input device to a 16 kHz mono WAV file.

Usage (within a QThread or main thread):
    rec = Recorder()
    rec.level_changed.connect(lambda rms: update_meter(rms))
    rec.error_occurred.connect(handle_error)
    rec.start(device_index=None)   # None = default device
    ...
    path = rec.stop()              # returns path to WAV file
"""

from __future__ import annotations

import queue
import math
import struct
import threading
import wave
from datetime import datetime
from typing import Callable, Optional

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
    error_occurred(str)   — error message
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
        self._stream = None
        self._wav: Optional[wave.Wave_write] = None
        self._output_path: Optional[str] = None
        self._lock = threading.Lock()
        self._recording = threading.Event()
        self._paused = threading.Event()
        self._q: queue.Queue = queue.Queue()
        self._writer_thread: Optional[threading.Thread] = None
        self._elapsed_frames: int = 0
        # Optional live adapter hook. It must be non-blocking: this is called
        # from PortAudio's callback thread and is deliberately absent for the
        # legacy recorder path.
        self._frame_sink = frame_sink
        # Live transcription needs microphone PCM but must not silently turn
        # every meeting into a recording.  Keep the legacy default intact and
        # allow its adapter to opt out of all file/queue writer work.
        self._write_wav = bool(write_wav)

    # ------------------------------------------------------------------ public

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
        self._output_path = None
        if self._write_wav:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            # Microseconds avoid overwriting a just-finished recording when
            # the user starts another one within the same second.
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
            self._output_path = str(_DATA_DIR / f"REC_{ts}.wav")
            try:
                self._wav = wave.open(self._output_path, "wb")
                self._wav.setnchannels(_CHANNELS)
                self._wav.setsampwidth(2)    # int16 → 2 bytes
                self._wav.setframerate(_SAMPLE_RATE)
            except OSError as exc:
                self.error_occurred.emit(f"Cannot create recording file: {exc}")
                return

        self._recording.set()
        self._paused.clear()
        # Fresh queue — discard any stale sentinel from a previous stop() call
        self._q = queue.Queue()

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
            if self._wav is not None:
                try:
                    self._wav.close()
                except Exception as close_exc:
                    logger.debug("WAV close during stream-open failure: %s", close_exc)
                self._wav = None
            # A stream-open failure creates only a WAV header.  Do not hand
            # that zero-frame path to the widget's error handler as a usable
            # recording.
            failed_path = self._output_path
            self._output_path = None
            if failed_path:
                try:
                    import os
                    os.unlink(failed_path)
                except OSError:
                    pass
            self.error_occurred.emit(f"Cannot open audio device: {exc}")
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
        """Stop recording and return the path to the WAV file."""
        # Error paths may call stop() after stream initialisation failed.
        # There is no complete file in that state.
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
        if self._writer_thread is not None:
            self._q.put(None)
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=5)
            self._writer_thread = None

        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None

        path = self._output_path
        logger.info("Recording stopped: %s (%d frames)", path, self._elapsed_frames)
        return path

    def is_recording(self) -> bool:
        return self._recording.is_set()

    @property
    def elapsed_seconds(self) -> float:
        return self._elapsed_frames / _SAMPLE_RATE

    # ------------------------------------------------------------------ internal

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            logger.debug("Audio stream status: %s", status)
        if not self._paused.is_set():
            try:
                chunk = bytes(indata)
                if self._write_wav:
                    self._q.put(chunk)
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
        """Background thread: drains queue and writes to WAV."""
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
