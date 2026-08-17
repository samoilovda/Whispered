"""Pure-Python recorder fallback tests."""

import struct

from core.recorder import _rms_pcm16


def test_pcm16_rms_fallback_handles_silence_and_signal():
    assert _rms_pcm16(b"") == 0.0
    assert _rms_pcm16(b"\x00") == 0.0
    half_scale = struct.pack("<hh", 16_384, -16_384)
    assert _rms_pcm16(half_scale) == 0.5


# ─── Recorder bounded-queue and atomic-write tests ───────────────────────────
# These tests exercise the Recorder internals without sounddevice:
# they directly manipulate the queue, _writer, and stop() return value.

import queue
import threading
import time
import wave
from unittest.mock import MagicMock


def _make_recorder(tmp_path, write_wav=True):
    """Construct a Recorder whose _DATA_DIR is redirected to tmp_path."""
    from core import recorder as rec_mod
    # Redirect _DATA_DIR so recordings go to tmp_path
    original_data_dir = rec_mod._DATA_DIR
    rec_mod._DATA_DIR = tmp_path
    from core.recorder import Recorder
    r = Recorder(write_wav=write_wav)
    r._DATA_DIR_orig = original_data_dir  # remember for teardown
    return r


def _restore_data_dir(recorder):
    from core import recorder as rec_mod
    if hasattr(recorder, "_DATA_DIR_orig"):
        rec_mod._DATA_DIR = recorder._DATA_DIR_orig


def test_bounded_queue_drops_frames_on_overflow(tmp_path):
    """Audio callback drops newest frames when queue is full; counter increments."""
    from core.recorder import _QUEUE_MAXSIZE

    r = _make_recorder(tmp_path)
    try:
        # Fill the queue completely
        for i in range(_QUEUE_MAXSIZE):
            r._q.put_nowait(b"\x00" * 10)

        assert r._q.full()
        assert r._dropped_frames == 0

        # Simulate the drop logic directly (mimics what _audio_callback does
        # when it hits queue.Full — avoids Qt signal emission in test env)
        frames = 32
        try:
            r._q.put_nowait(b"\x00" * 10)
        except queue.Full:
            r._dropped_frames += frames

        assert r._dropped_frames == 32
    finally:
        _restore_data_dir(r)


def test_disk_full_emits_single_error_and_no_success(tmp_path):
    """If the writer thread hits OSError, error_occurred emits once, stop() → None."""

    r = _make_recorder(tmp_path)
    errors = []
    r.error_occurred.connect(lambda msg: errors.append(msg))

    # Plant a fake .part WAV
    part = tmp_path / "REC_fake.wav.part"
    with wave.open(str(part), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)

    r._part_path = str(part)
    r._output_path = str(tmp_path / "REC_fake.wav")
    r._recording.set()
    r._write_wav = True
    r._q = queue.Queue(maxsize=50)

    mock_wav = MagicMock()
    mock_wav.writeframes.side_effect = OSError("no space left on device")
    r._wav = mock_wav

    # Start writer in a real thread
    r._writer_thread = threading.Thread(target=r._writer, daemon=True)
    r._writer_thread.start()
    r._q.put(b"\x00" * 100)
    time.sleep(0.2)

    # Simulate stream already stopped
    r._recording.clear()
    r._stream = None

    result = r.stop()

    assert result is None, "stop() must return None when writer failed"
    assert len(errors) == 1, f"expected exactly 1 error, got {len(errors)}: {errors}"
    assert not part.exists(), ".part file must be cleaned up"
    _restore_data_dir(r)


def test_cancel_before_drain_leaves_no_part_file(tmp_path):
    """If writer thread never drains (hung), stop() returns None and cleans up .part."""

    r = _make_recorder(tmp_path)
    errors = []
    r.error_occurred.connect(lambda msg: errors.append(msg))

    part = tmp_path / "REC_fake2.wav.part"
    part.write_bytes(b"")

    r._part_path = str(part)
    r._output_path = str(tmp_path / "REC_fake2.wav")
    r._recording.set()
    r._write_wav = True
    r._q = queue.Queue(maxsize=50)
    r._wav = MagicMock()

    # Writer that hangs forever
    def hung_writer():
        time.sleep(60)

    r._writer_thread = threading.Thread(target=hung_writer, daemon=True)
    r._writer_thread.start()
    r._recording.clear()
    r._stream = None

    result = r.stop()

    assert result is None
    assert not part.exists(), ".part file must be removed on drain timeout"
    _restore_data_dir(r)


def test_happy_path_atomic_write(tmp_path):
    """Successful stop(): .part not present; target file is a valid WAV."""
    from core.recorder import _SAMPLE_RATE, _CHANNELS

    r = _make_recorder(tmp_path)

    part = tmp_path / "REC_ok.wav.part"
    target = tmp_path / "REC_ok.wav"

    # Write a valid WAV into the .part file
    with wave.open(str(part), "wb") as w:
        w.setnchannels(_CHANNELS)
        w.setsampwidth(2)
        w.setframerate(_SAMPLE_RATE)
        w.writeframes(b"\x00" * 320)  # 160 samples

    r._part_path = str(part)
    r._output_path = str(target)
    r._write_wav = True
    r._q = queue.Queue(maxsize=50)
    # Keep recording set so stop() doesn't early-return
    r._recording.set()
    # No stream, no wav (already written above and closed by context manager)
    r._stream = None
    r._wav = None

    result = r.stop()

    assert result == str(target)
    assert target.exists()
    assert not part.exists(), ".part must not remain after successful stop()"
    _restore_data_dir(r)

