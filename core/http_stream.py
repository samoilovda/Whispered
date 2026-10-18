"""Cancellable urllib streams with inherited proxy and default TLS trust.

The reader owns response.close(). The controller interrupts the underlying
socket instead of waiting for the buffered reader's lock inside close().
Each request gets its own opener; no global urllib handlers are changed.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import threading
import urllib.request
from typing import Any, Callable


class CancellableHTTPStream:
    """Track transport sockets before headers, including proxy connections."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stopped = False
        self._sockets: list[socket.socket] = []

    @staticmethod
    def _shutdown(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass  # already closed, or detached by TLS wrapping

    def _track(self, sock: socket.socket | None) -> None:
        if sock is None:
            return
        with self._lock:
            self._sockets.append(sock)
            stopped = self._stopped
        if stopped:
            self._shutdown(sock)

    def connection(self, cls: type) -> Callable[..., Any]:
        """Wrap socket creation and TLS wrapping on this connection only."""
        def make(*args: Any, **kwargs: Any) -> Any:
            conn = cls(*args, **kwargs)
            create = conn._create_connection

            def create_socket(*args: Any, **kwargs: Any) -> socket.socket:
                sock = create(*args, **kwargs)
                self._track(sock)
                return sock

            conn._create_connection = create_socket
            connect = conn.connect

            def tracked_connect() -> None:
                connect()
                self._track(conn.sock)

            conn.connect = tracked_connect
            return conn

        return make

    def open(self, request: urllib.request.Request, *, timeout: float) -> Any:
        """Open normally with urllib's environment proxy and CA handling."""
        opener = urllib.request.build_opener(_HTTPHandler(self), _HTTPSHandler(self))
        return opener.open(request, timeout=timeout)

    def abort(self) -> None:
        """Interrupt reads without acquiring any response/reader lock."""
        with self._lock:
            self._stopped = True
            sockets = list(self._sockets)
        for sock in sockets:
            self._shutdown(sock)


class _HTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, stream: CancellableHTTPStream) -> None:
        super().__init__()
        self.stream = stream

    def http_open(self, req: Any) -> Any:
        return self.do_open(self.stream.connection(http.client.HTTPConnection), req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, stream: CancellableHTTPStream) -> None:
        self._stream_context = ssl.create_default_context()
        self._stream_context.set_alpn_protocols(["http/1.1"])
        wrap = self._stream_context.wrap_socket

        def wrap_socket(
            sock: socket.socket, server_side: bool = False, do_handshake_on_connect: bool = True,
            suppress_ragged_eofs: bool = True, server_hostname: str | None = None,
            session: ssl.SSLSession | None = None,
        ) -> ssl.SSLSocket:
            # Track the TLS socket before its handshake can block. The raw
            # socket tracked by create_connection is detached by wrap_socket.
            secured = wrap(
                sock, server_side=server_side, do_handshake_on_connect=False,
                suppress_ragged_eofs=suppress_ragged_eofs, server_hostname=server_hostname,
                session=session,
            )
            stream._track(secured)
            if do_handshake_on_connect:
                secured.do_handshake()
            return secured

        setattr(self._stream_context, "wrap_socket", wrap_socket)
        super().__init__(context=self._stream_context)
        self.stream = stream

    def https_open(self, req: Any) -> Any:
        return self.do_open(
            self.stream.connection(http.client.HTTPSConnection), req,
            context=self._stream_context,
        )
