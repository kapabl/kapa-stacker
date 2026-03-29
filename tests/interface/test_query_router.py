"""Tests for daemon query router."""

import unittest

from src.interface.daemon.protocol import DaemonRequest
from src.interface.daemon.query_router import QueryRouter


class TestQueryRouter(unittest.TestCase):

    def test_routes_to_handler(self):
        def mock_analyze(params):
            return {"branch": params.get("base", "main"), "prs": []}

        router = QueryRouter({"analyze": mock_analyze})
        request = DaemonRequest(action="analyze", params={"base": "develop"})
        response = router.handle(request)

        self.assertEqual(response.status, "ok")
        self.assertEqual(response.data["branch"], "develop")

    def test_unknown_action(self):
        router = QueryRouter({})
        request = DaemonRequest(action="nonexistent")
        response = router.handle(request)

        self.assertEqual(response.status, "error")
        self.assertIn("Unknown action", response.error)

    def test_handler_exception(self):
        def failing_handler(params):
            raise ValueError("something broke")

        router = QueryRouter({"fail": failing_handler})
        request = DaemonRequest(action="fail")
        response = router.handle(request)

        self.assertEqual(response.status, "error")
        self.assertIn("something broke", response.error)


if __name__ == "__main__":
    unittest.main()
