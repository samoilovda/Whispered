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

import os
import queue
import threading
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import get_logger

logger = get_logger(__name__)

_SAMPLE_RATE = 16_000      # Whisper's native sample rate
_CHANNELS = 1              # Mono
_DTYPE = "int16"           # 16-bit PCM
_BLOCKSIZE = 1_600         # 100 ms chunks → level at 10 Hz
_DATA_DIR = Path.home() / ".whisper-fedora" / "recordings"


def _rms(block) -> float:
    """Root-mean-square of a numpy int16 block, normalised to [0, 1]."""
    import numpy as np
    arr = block.astype("float32") / 32768.0
    return float(np.sqrt(np.mean(arr ** 2)))


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

    def __init__(self, parent=None):
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

        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._output_path = str(_DATA_DIR / f"REC_{ts}.wav")
        self._elapsed_frames = 0

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
                except Exception:
                    pass
                self._wav = None
            self.error_occurred.emit(f"Cannot open audio device: {exc}")
            return

        # Writer thread — started only after the stream is confirmed open
        self._writer_thread = threading.Thread(target=self._writer, daemon=True)
        self._writer_thread.start()
        logger.info("Recording started: %s", self._output_path)

    def pause(self) -> None:
        """Pause recording (frames are discarded while paused)."""
        self._paused.set()

    def resume(self) -> None:
        """Resume after pause."""
        self._paused.clear()

    def stop(self) -> Optional[str]:
        """Stop recording and return the path to the WAV file."""
        self._recording.clear()

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        # Signal writer thread to finish
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
            self._q.put(bytes(indata))
            self.level_changed.emit(_rms(indata))

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
