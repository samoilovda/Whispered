"""Real-socket cancellation regressions: no external server or model."""

import http.server
import importlib
from pathlib import Path
import threading
import socketserver

import pytest

from core.http_stream import CancellableHTTPStream


@pytest.mark.parametrize("phase", ["headers", "body", "tls-handshake"])
def test_cancellation_interrupts_socket_before_headers_and_during_body(phase):
    # Other suites install client stubs at collection time. Load the real
    # source in isolation without replacing their module bindings.
    spec = importlib.util.spec_from_file_location(
        "http_stream_test_client", Path(__file__).resolve().parents[1] / "core" / "lm_client.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    entered = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            if phase == "body":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.flush()
            entered.set()
            release.wait(10)

        def log_message(self, *args):
            pass

    class TLSStall(socketserver.BaseRequestHandler):
        def handle(self):
            entered.set()
            release.wait(10)

    server = (socketserver.TCPServer(("127.0.0.1", 0), TLSStall) if phase == "tls-handshake"
              else http.server.HTTPServer(("127.0.0.1", 0), Handler))
    serving = threading.Thread(target=server.handle_request, daemon=True)
    serving.start()
    results = []
    errors = []
    finished = threading.Event()

    def complete():
        try:
            scheme = "https" if phase == "tls-handshake" else "http"
            client = module.LMStudioClient(f"{scheme}://127.0.0.1:{server.server_address[1]}/v1")
            results.append(client.chat_completion_stream(
                [{"role": "user", "content": "synthetic"}], timeout=30,
                is_cancelled=cancelled.is_set,
            ))
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    before = set(threading.enumerate())
    caller = threading.Thread(target=complete, daemon=True)
    caller.start()
    try:
        assert entered.wait(3), "local HTTP request never arrived"
        cancelled.set()
        assert finished.wait(2), "cancel waited for the stalled network read"
        assert not errors
        assert results == [None]
        assert not [t for t in threading.enumerate() if t not in before and t.name == "lm-studio-stream"]
    finally:
        release.set()
        cancelled.set()
        caller.join(5)
        serving.join(5)
        server.server_close()


def test_abort_also_interrupts_a_socket_attached_after_cancellation():
    import socket

    left, right = socket.socketpair()
    try:
        transport = CancellableHTTPStream()
        transport.abort()
        transport._track(left)
        assert right.recv(1) == b""
    finally:
        left.close()
        right.close()
