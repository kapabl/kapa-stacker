"""Daemon client — connects to running daemon, sends queries."""

from __future__ import annotations

import os
import socket

from src.interface.daemon.protocol import (
    SOCKET_PATH,
    HEADER_SIZE,
    DaemonRequest,
    DaemonResponse,
)


def is_daemon_running(socket_path: str = SOCKET_PATH) -> bool:
    """Check if a daemon is listening on the socket."""
    if not os.path.exists(socket_path):
        return False
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(1.0)
        conn.connect(socket_path)
        conn.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


def send_query(
    action: str,
    params: dict | None = None,
    socket_path: str = SOCKET_PATH,
) -> DaemonResponse:
    """Send a query to the daemon and return the response."""
    request = DaemonRequest(action=action, params=params or {})

    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(30.0)
    try:
        conn.connect(socket_path)
        conn.sendall(request.serialize())
        return _recv_response(conn)
    finally:
        conn.close()


def _recv_response(conn: socket.socket) -> DaemonResponse:
    """Read a length-prefixed response from the daemon."""
    header = _recv_exact(conn, HEADER_SIZE)
    if not header:
        return DaemonResponse.fail("No response from daemon")
    length = int.from_bytes(header, "big")
    payload = _recv_exact(conn, length)
    if not payload:
        return DaemonResponse.fail("Incomplete response from daemon")
    return DaemonResponse.deserialize(payload)


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
