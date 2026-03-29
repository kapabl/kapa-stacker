"""Tests for daemon server + client integration."""

import tempfile
import threading
import time
import unittest

from src.interface.daemon.server import DaemonServer
from src.interface.daemon.client import is_daemon_running, send_query
from src.interface.daemon.query_router import QueryRouter


def _wait_for_daemon(socket_path: str, timeout: float = 2.0) -> bool:
    """Poll until daemon is reachable or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_daemon_running(socket_path):
            return True
        time.sleep(0.05)
    return False


class TestDaemonIntegration(unittest.TestCase):

    def _start_server(self, router, socket_path):
        server = DaemonServer(router, socket_path=socket_path)
        thread = threading.Thread(target=server.start, daemon=True)
        thread.start()
        self.assertTrue(_wait_for_daemon(socket_path))
        return server, thread

    def test_server_client_round_trip(self):
        socket_path = tempfile.mktemp(suffix=".sock")

        def mock_status(params):
            return {"running": True, "lsp_servers": []}

        router = QueryRouter({"status": mock_status})
        server, thread = self._start_server(router, socket_path)

        try:
            response = send_query(
                "status", socket_path=socket_path,
            )
            self.assertEqual(response.status, "ok")
            self.assertTrue(response.data["running"])
        finally:
            server.stop()
            thread.join(timeout=3)

    def test_daemon_not_running(self):
        socket_path = tempfile.mktemp(suffix=".sock")
        self.assertFalse(is_daemon_running(socket_path))

    def test_unknown_action_returns_error(self):
        socket_path = tempfile.mktemp(suffix=".sock")
        router = QueryRouter({})
        server, thread = self._start_server(router, socket_path)

        try:
            response = send_query(
                "bogus", socket_path=socket_path,
            )
            self.assertEqual(response.status, "error")
            self.assertIn("Unknown action", response.error)
        finally:
            server.stop()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
