"""Daemon server — listens on unix socket, dispatches queries."""

from __future__ import annotations

import os
import signal
import socket
import threading

from src.interface.daemon.protocol import (
    SOCKET_PATH,
    HEADER_SIZE,
    DaemonRequest,
    DaemonResponse,
)
from src.interface.daemon.query_router import QueryRouter


class DaemonServer:
    """Unix socket server that routes queries to use cases."""

    def __init__(
        self,
        router: QueryRouter,
        socket_path: str = SOCKET_PATH,
        on_start: callable | None = None,
        on_stop: callable | None = None,
    ):
        self._router = router
        self._socket_path = socket_path
        self._on_start = on_start
        self._on_stop = on_stop
        self._running = False
        self._server_socket: socket.socket | None = None

    def start(self) -> None:
        """Start listening. Blocks until stop() is called."""
        if self._on_start:
            self._on_start()
        self._cleanup_stale_socket()
        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket.bind(self._socket_path)
        self._server_socket.listen(5)
        self._server_socket.settimeout(1.0)
        self._running = True

        self._register_signals()

        while self._running:
            try:
                conn, _ = self._server_socket.accept()
                thread = threading.Thread(
                    target=self._handle_connection,
                    args=(conn,),
                    daemon=True,
                )
                thread.start()
            except socket.timeout:
                continue
            except OSError:
                break

        self._shutdown()

    def stop(self) -> None:
        self._running = False

    def _handle_connection(self, conn: socket.socket) -> None:
        try:
            raw = _recv_message(conn)
            if not raw:
                return
            request = DaemonRequest.deserialize(raw)
            response = self._router.handle(request)
            conn.sendall(response.serialize())
        except Exception as exc:
            error_response = DaemonResponse.fail(str(exc))
            conn.sendall(error_response.serialize())
        finally:
            conn.close()

    def _register_signals(self) -> None:
        """Register signal handlers. Only works in main thread."""
        try:
            signal.signal(signal.SIGTERM, self._handle_signal)
            signal.signal(signal.SIGINT, self._handle_signal)
        except ValueError:
            pass  # not in main thread — signals handled externally

    def _handle_signal(self, signum, frame) -> None:
        self.stop()

    def _cleanup_stale_socket(self) -> None:
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)

    def _shutdown(self) -> None:
        if self._on_stop:
            self._on_stop()
        if self._server_socket:
            self._server_socket.close()
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)


def _recv_message(conn: socket.socket) -> bytes | None:
    """Read a length-prefixed message from the socket."""
    header = _recv_exact(conn, HEADER_SIZE)
    if not header:
        return None
    length = int.from_bytes(header, "big")
    return _recv_exact(conn, length)


def _recv_exact(conn: socket.socket, size: int) -> bytes | None:
    """Read exactly `size` bytes from socket."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = conn.recv(min(remaining, 4096))
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
