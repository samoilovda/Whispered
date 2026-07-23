"""Pure-Python recorder fallback tests."""

import struct

from core.recorder import _rms_pcm16


def test_pcm16_rms_fallback_handles_silence_and_signal():
    assert _rms_pcm16(b"") == 0.0
    assert _rms_pcm16(b"\x00") == 0.0
    half_scale = struct.pack("<hh", 16_384, -16_384)
    assert _rms_pcm16(half_scale) == 0.5
