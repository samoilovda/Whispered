"""Tests for the L8 system-capture IPC contract."""

from __future__ import annotations

import struct

import pytest

from core.live.system_capture_protocol import (
    CaptureLifecycle,
    CaptureTarget,
    FrameDecoder,
    MessageType,
    ProtocolError,
    audio_frame,
    encode_frame,
    hello_frame,
    start_frame,
    stop_frame,
)


def test_audio_frame_round_trips_through_fragmented_decoder():
    wire = audio_frame(
        sequence=4,
        source_timestamp=10.5,
        monotonic_timestamp=20.5,
        pcm=b"\x01\x02\x03\x04",
    )
    decoder = FrameDecoder()
    decoded = []
    for index in range(0, len(wire), 3):
        decoded.extend(decoder.feed(wire[index : index + 3]))

    assert len(decoded) == 1
    frame = decoded[0]
    assert frame.message_type is MessageType.AUDIO
    assert frame.header["sequence"] == 4
    assert frame.header["payload_bytes"] == 4
    assert frame.payload == b"\x01\x02\x03\x04"
    assert decoder.buffered_bytes == 0


def test_decoder_accepts_multiple_control_frames_in_one_read():
    wire = hello_frame(123, {"audio", "screen_capture_kit"}) + stop_frame("test")
    frames = FrameDecoder().feed(wire)
    assert [frame.message_type for frame in frames] == [MessageType.HELLO, MessageType.STOP]
    assert frames[0].header["capabilities"] == ["audio", "screen_capture_kit"]


def test_lifecycle_requires_start_ack_and_stop_ack():
    lifecycle = CaptureLifecycle()
    lifecycle.consume(FrameDecoder().feed(hello_frame(1, {"audio"}))[0])
    lifecycle.consume(FrameDecoder().feed(start_frame(CaptureTarget(bundle_id="com.zoom")))[0])
    lifecycle.consume(
        FrameDecoder().feed(encode_frame(MessageType.STARTED, {"sample_rate": 16_000}))[0]
    )
    lifecycle.consume(
        FrameDecoder().feed(audio_frame(
            sequence=0,
            source_timestamp=0.0,
            monotonic_timestamp=1.0,
            pcm=b"12",
        ))[0]
    )
    lifecycle.consume(FrameDecoder().feed(stop_frame())[0])
    lifecycle.consume(FrameDecoder().feed(encode_frame(MessageType.STOPPED))[0])

    assert lifecycle.state == "stopped"
    assert lifecycle.audio_frames == 1


def test_audio_after_stop_is_rejected_and_target_needs_a_selector():
    with pytest.raises(ProtocolError, match="cannot consume 'audio'"):
        lifecycle = CaptureLifecycle()
        lifecycle.consume(FrameDecoder().feed(hello_frame(1, {"audio"}))[0])
        lifecycle.consume(FrameDecoder().feed(start_frame(CaptureTarget(process_id=99)))[0])
        lifecycle.consume(FrameDecoder().feed(encode_frame(MessageType.STARTED))[0])
        lifecycle.consume(FrameDecoder().feed(stop_frame())[0])
        lifecycle.consume(FrameDecoder().feed(encode_frame(MessageType.STOPPED))[0])
        lifecycle.consume(FrameDecoder().feed(audio_frame(
            sequence=1,
            source_timestamp=1.0,
            monotonic_timestamp=2.0,
            pcm=b"12",
        ))[0])

    with pytest.raises(ValueError, match="needs"):
        CaptureTarget()


def test_invalid_header_version_and_payload_are_rejected():
    wire = encode_frame(MessageType.PING)
    prefix_size = struct.calcsize("!4sII")
    magic, header_size, payload_size = struct.unpack("!4sII", wire[:prefix_size])
    header = bytearray(wire[prefix_size : prefix_size + header_size])
    header = header.replace(b'"version":1', b'"version":99')
    invalid = struct.pack("!4sII", magic, len(header), payload_size) + header
    with pytest.raises(ProtocolError, match="version"):
        FrameDecoder().feed(invalid)

    with pytest.raises(ProtocolError, match="cannot carry"):
        encode_frame(MessageType.PING, payload=b"unexpected")


# ─── Nonce handshake tests ────────────────────────────────────────────────────

from core.live.system_capture_protocol import generate_nonce


def test_generate_nonce_is_64_hex_chars():
    """generate_nonce returns a 64-character hex string (32 bytes)."""
    nonce = generate_nonce()
    assert isinstance(nonce, str)
    assert len(nonce) == 64
    assert all(c in "0123456789abcdef" for c in nonce)


def test_generate_nonce_is_unique():
    """Two consecutive nonces must differ (birthday bound is negligible)."""
    assert generate_nonce() != generate_nonce()


def test_hello_frame_with_nonce_roundtrips():
    """hello_frame with a nonce survives encode → decode with the nonce intact."""
    nonce = generate_nonce()
    wire = hello_frame(42, {"audio"}, nonce=nonce)
    frames = FrameDecoder().feed(wire)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.message_type is MessageType.HELLO
    assert frame.header["nonce"] == nonce
    assert frame.header["helper_pid"] == 42


def test_hello_frame_without_nonce_has_no_nonce_field():
    """hello_frame without nonce arg omits nonce from the wire frame."""
    wire = hello_frame(42, {"audio"})
    frames = FrameDecoder().feed(wire)
    assert len(frames) == 1
    assert "nonce" not in frames[0].header


def test_nonce_env_var_passthrough(tmp_path):
    """launch_helper passes WHISPERED_CAPTURE_NONCE to helper subprocess env."""
    from unittest.mock import patch, MagicMock

    captured_env: dict = {}

    def mock_popen(cmd, env=None, **kwargs):
        captured_env.update(env or {})
        mock = MagicMock()
        mock.poll.return_value = None
        return mock

    with patch("subprocess.Popen", side_effect=mock_popen):
        from core.live.system_audio_source import SystemAudioSource
        source = SystemAudioSource(
            socket_path=str(tmp_path / "test.sock"),
            enabled=True,
            helper_command=["echo", "fake"],
        )
        source._ipc_nonce = "aabbcc1234567890" * 4  # 64 char hex
        source._launch_helper()

    assert captured_env.get("WHISPERED_CAPTURE_NONCE") == source._ipc_nonce
