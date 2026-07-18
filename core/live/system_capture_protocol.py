"""Versioned IPC contract for the macOS system-audio capture helper.

Wire format (network byte order):

    magic[4] | header_length[uint32] | payload_length[uint32]
    header[UTF-8 JSON] | payload[bytes]

Control frames have an empty payload. Audio frames carry signed little-endian
PCM and keep timestamps/sequence numbers in the JSON header.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from enum import Enum
from typing import Any

PROTOCOL_NAME = "whispered.system-audio"
PROTOCOL_VERSION = 1
MAGIC = b"WSCA"
_PREFIX = struct.Struct("!4sII")
MAX_HEADER_BYTES = 16 * 1024
MAX_PAYLOAD_BYTES = 1024 * 1024


class ProtocolError(ValueError):
    """Raised when an IPC frame violates the versioned contract."""


class MessageType(str, Enum):
    HELLO = "hello"
    START = "start"
    STARTED = "started"
    AUDIO = "audio"
    METER = "meter"
    ERROR = "error"
    STOP = "stop"
    STOPPED = "stopped"
    PING = "ping"
    PONG = "pong"


@dataclass(frozen=True, slots=True)
class IPCFrame:
    """Decoded and validated one wire frame."""

    message_type: MessageType
    header: dict[str, Any]
    payload: bytes = b""

    @property
    def is_audio(self) -> bool:
        return self.message_type is MessageType.AUDIO


@dataclass(frozen=True, slots=True)
class CaptureTarget:
    """Application/window selection sent from Whispered to the helper."""

    bundle_id: str | None = None
    process_id: int | None = None
    window_id: int | None = None

    def __post_init__(self) -> None:
        if not any((self.bundle_id, self.process_id, self.window_id)):
            raise ValueError("CaptureTarget needs bundle_id, process_id, or window_id")
        if self.process_id is not None and self.process_id < 1:
            raise ValueError("process_id must be positive")
        if self.window_id is not None and self.window_id < 1:
            raise ValueError("window_id must be positive")

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "bundle_id": self.bundle_id,
                "process_id": self.process_id,
                "window_id": self.window_id,
            }.items()
            if value is not None
        }


class FrameDecoder:
    """Incremental decoder for fragmented Unix-socket reads."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[IPCFrame]:
        self._buffer.extend(data)
        frames: list[IPCFrame] = []
        while True:
            if len(self._buffer) < _PREFIX.size:
                break
            magic, header_length, payload_length = _PREFIX.unpack_from(self._buffer)
            if magic != MAGIC:
                raise ProtocolError("invalid system capture IPC magic")
            if header_length > MAX_HEADER_BYTES:
                raise ProtocolError("IPC header is too large")
            if payload_length > MAX_PAYLOAD_BYTES:
                raise ProtocolError("IPC payload is too large")
            total = _PREFIX.size + header_length + payload_length
            if len(self._buffer) < total:
                break

            start = _PREFIX.size
            header_bytes = bytes(self._buffer[start : start + header_length])
            payload_start = start + header_length
            payload = bytes(self._buffer[payload_start:total])
            del self._buffer[:total]
            frames.append(_decode_frame(header_bytes, payload))
        return frames

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)


class CaptureLifecycle:
    """Small state machine shared by helper/client tests and the future adapter."""

    def __init__(self) -> None:
        self.state = "idle"
        self.last_error: str | None = None
        self.audio_frames = 0

    def consume(self, frame: IPCFrame) -> None:
        kind = frame.message_type
        if kind is MessageType.HELLO:
            self._expect({"idle"}, kind)
            self.state = "hello"
        elif kind is MessageType.START:
            self._expect({"hello", "stopped"}, kind)
            self.state = "starting"
        elif kind is MessageType.STARTED:
            self._expect({"starting"}, kind)
            self.state = "running"
        elif kind is MessageType.AUDIO or kind is MessageType.METER:
            self._expect({"running"}, kind)
            if kind is MessageType.AUDIO:
                self.audio_frames += 1
        elif kind is MessageType.STOP:
            self._expect({"running", "starting"}, kind)
            self.state = "stopping"
        elif kind is MessageType.STOPPED:
            self._expect({"stopping", "hello", "stopped"}, kind)
            self.state = "stopped"
        elif kind is MessageType.ERROR:
            self.last_error = str(frame.header.get("message", "unknown helper error"))
            self.state = "failed"
        elif kind in {MessageType.PING, MessageType.PONG}:
            return

    def prepare_start(self) -> None:
        """Record the app-side start command before sending it."""
        self._expect({"hello", "stopped"}, MessageType.START)
        self.state = "starting"

    def prepare_stop(self) -> None:
        """Record the app-side stop command before sending it."""
        self._expect({"running", "starting"}, MessageType.STOP)
        self.state = "stopping"

    def _expect(self, allowed: set[str], kind: MessageType) -> None:
        if self.state not in allowed:
            raise ProtocolError(
                f"cannot consume {kind.value!r} while capture state is {self.state!r}"
            )


def encode_frame(
    message_type: MessageType | str,
    header: dict[str, Any] | None = None,
    payload: bytes = b"",
) -> bytes:
    """Encode one validated protocol frame."""
    try:
        kind = MessageType(message_type)
    except ValueError as exc:
        raise ProtocolError(f"unknown message type: {message_type!r}") from exc
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ProtocolError("IPC payload is too large")

    envelope = {
        "protocol": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
        "type": kind.value,
        **(header or {}),
    }
    if kind is MessageType.AUDIO:
        envelope.setdefault("sample_rate", 16_000)
        envelope.setdefault("channels", 1)
        envelope.setdefault("sample_format", "s16le")
        envelope["payload_bytes"] = len(payload)
    elif payload:
        raise ProtocolError(f"{kind.value} frame cannot carry a payload")

    try:
        encoded_header = json.dumps(
            envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"IPC header is not JSON-serializable: {exc}") from exc
    if len(encoded_header) > MAX_HEADER_BYTES:
        raise ProtocolError("IPC header is too large")
    return _PREFIX.pack(MAGIC, len(encoded_header), len(payload)) + encoded_header + payload


def hello_frame(helper_pid: int, capabilities: set[str]) -> bytes:
    return encode_frame(
        MessageType.HELLO,
        {"helper_pid": helper_pid, "capabilities": sorted(capabilities)},
    )


def start_frame(target: CaptureTarget, *, sample_rate: int = 16_000, channels: int = 1) -> bytes:
    if sample_rate < 1 or channels < 1:
        raise ProtocolError("sample_rate and channels must be positive")
    return encode_frame(
        MessageType.START,
        {"target": target.as_dict(), "sample_rate": sample_rate, "channels": channels},
    )


def stop_frame(reason: str = "user") -> bytes:
    if not reason:
        raise ProtocolError("stop reason must not be empty")
    return encode_frame(MessageType.STOP, {"reason": reason})


def audio_frame(
    *,
    sequence: int,
    source_timestamp: float,
    monotonic_timestamp: float,
    pcm: bytes,
    sample_rate: int = 16_000,
    channels: int = 1,
) -> bytes:
    if sequence < 0:
        raise ProtocolError("audio sequence must be non-negative")
    if source_timestamp < 0 or monotonic_timestamp < 0:
        raise ProtocolError("audio timestamps must be non-negative")
    if sample_rate < 1 or channels < 1:
        raise ProtocolError("audio format must be positive")
    return encode_frame(
        MessageType.AUDIO,
        {
            "sequence": sequence,
            "source_timestamp": source_timestamp,
            "monotonic_timestamp": monotonic_timestamp,
            "sample_rate": sample_rate,
            "channels": channels,
            "sample_format": "s16le",
        },
        pcm,
    )


def _decode_frame(header_bytes: bytes, payload: bytes) -> IPCFrame:
    try:
        header = json.loads(header_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"invalid IPC JSON header: {exc}") from exc
    if not isinstance(header, dict):
        raise ProtocolError("IPC header must be a JSON object")
    if header.get("protocol") != PROTOCOL_NAME:
        raise ProtocolError("unsupported system capture protocol")
    if header.get("version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported system capture protocol version")
    try:
        kind = MessageType(header["type"])
    except (KeyError, ValueError) as exc:
        raise ProtocolError("IPC header has an unknown message type") from exc

    if kind is MessageType.AUDIO:
        if header.get("payload_bytes") != len(payload):
            raise ProtocolError("audio payload_bytes does not match wire payload")
        for field_name in (
            "sequence",
            "source_timestamp",
            "monotonic_timestamp",
            "sample_rate",
            "channels",
        ):
            if field_name not in header:
                raise ProtocolError(f"audio header lacks {field_name}")
    elif payload:
        raise ProtocolError(f"{kind.value} frame unexpectedly has a payload")
    return IPCFrame(kind, header, payload)
