"""Unit tests for the L9 system audio source; no macOS helper required."""

from __future__ import annotations

import queue
import threading
import time

from core.live import (
    CaptureTarget,
    MessageType,
    SystemAudioSource,
    audio_frame,
    encode_frame,
    hello_frame,
)


class _FakeSocket:
    def __init__(self):
        self.incoming: queue.Queue[bytes] = queue.Queue()
        self.sent: list[bytes] = []
        self.closed = False

    def settimeout(self, _value):
        pass

    def connect(self, _path):
        pass

    def recv(self, _size):
        try:
            item = self.incoming.get(timeout=0.05)
        except queue.Empty:
            raise TimeoutError("fake timeout")
        if item is None:
            return b""
        return item

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


class _SocketFactory:
    def __init__(self, sock):
        self.sock = sock

    def __call__(self, _path):
        return self.sock


def _wait_for_sent(sock, count):
    deadline = time.monotonic() + 1.0
    while len(sock.sent) < count and time.monotonic() < deadline:
        time.sleep(0.005)


def test_system_source_handshakes_and_exposes_audio_meter_and_frames():
    sock = _FakeSocket()
    source = SystemAudioSource(
        socket_path="/tmp/whispered-test.sock",
        enabled=True,
        socket_factory=_SocketFactory(sock),
    )
    started: list[bool] = []
    levels: list[float] = []
    source.started.connect(lambda: started.append(True))
    source.level_changed.connect(levels.append)

    assert source.start(CaptureTarget(bundle_id="com.zoom")) is True
    sock.incoming.put(hello_frame(42, {"audio", "screen_capture_kit"}))
    _wait_for_sent(sock, 1)
    assert source.lifecycle_state == "starting"

    sock.incoming.put(encode_frame(MessageType.STARTED, {"sample_rate": 16_000}))
    sock.incoming.put(encode_frame(MessageType.METER, {"rms": 0.35}))
    sock.incoming.put(audio_frame(
        sequence=7,
        source_timestamp=3.0,
        monotonic_timestamp=4.0,
        pcm=b"\x01\x02\x03\x04",
    ))
    _wait_for_sent(sock, 1)

    frame = source.get_frame(timeout=1.0)
    assert frame is not None
    assert (frame.source, frame.sequence, frame.source_timestamp) == ("system", 7, 3.0)
    assert frame.pcm == b"\x01\x02\x03\x04"
    assert started == [True]
    assert levels == [0.35]
    source.cancel()


def test_permission_error_is_user_actionable():
    sock = _FakeSocket()
    source = SystemAudioSource(
        socket_path="/tmp/whispered-test.sock",
        enabled=True,
        socket_factory=_SocketFactory(sock),
    )
    errors: list[str] = []
    source.error_occurred.connect(errors.append)
    source.start(CaptureTarget(process_id=99))
    sock.incoming.put(hello_frame(42, {"audio"}))
    _wait_for_sent(sock, 1)
    sock.incoming.put(
        encode_frame(
            MessageType.ERROR,
            {"code": "screen_recording_denied", "message": "denied"},
        )
    )
    deadline = time.monotonic() + 1.0
    while not errors and time.monotonic() < deadline:
        time.sleep(0.005)
    assert errors and "Screen Recording permission" in errors[0]
    source.cancel()


def test_stop_sends_stop_and_waits_for_stopped_ack():
    sock = _FakeSocket()
    source = SystemAudioSource(
        socket_path="/tmp/whispered-test.sock",
        enabled=True,
        socket_factory=_SocketFactory(sock),
    )
    source.start(CaptureTarget(window_id=123))
    sock.incoming.put(hello_frame(42, {"audio"}))
    _wait_for_sent(sock, 1)
    sock.incoming.put(encode_frame(MessageType.STARTED))
    deadline = time.monotonic() + 1.0
    while source.lifecycle_state != "running" and time.monotonic() < deadline:
        time.sleep(0.005)

    stop_thread = threading.Thread(target=source.stop)
    stop_thread.start()
    _wait_for_sent(sock, 2)
    sock.incoming.put(encode_frame(MessageType.STOPPED))
    stop_thread.join(timeout=1.0)

    assert not stop_thread.is_alive()
    assert source.lifecycle_state == "stopped"
    assert sock.closed is True
