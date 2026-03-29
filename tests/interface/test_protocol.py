"""Tests for daemon protocol serialization."""

import unittest

from src.interface.daemon.protocol import DaemonRequest, DaemonResponse


class TestDaemonRequest(unittest.TestCase):

    def test_round_trip(self):
        request = DaemonRequest(action="analyze", params={"base": "master"})
        raw = request.serialize()
        restored = DaemonRequest.deserialize(raw[8:])  # skip header
        self.assertEqual(restored.action, "analyze")
        self.assertEqual(restored.params, {"base": "master"})

    def test_empty_params(self):
        request = DaemonRequest(action="status")
        raw = request.serialize()
        restored = DaemonRequest.deserialize(raw[8:])
        self.assertEqual(restored.action, "status")
        self.assertEqual(restored.params, {})


class TestDaemonResponse(unittest.TestCase):

    def test_ok_round_trip(self):
        response = DaemonResponse.ok({"branch": "main", "prs": []})
        raw = response.serialize()
        restored = DaemonResponse.deserialize(raw[8:])
        self.assertEqual(restored.status, "ok")
        self.assertEqual(restored.data["branch"], "main")

    def test_fail_round_trip(self):
        response = DaemonResponse.fail("something broke")
        raw = response.serialize()
        restored = DaemonResponse.deserialize(raw[8:])
        self.assertEqual(restored.status, "error")
        self.assertEqual(restored.error, "something broke")

    def test_ok_factory(self):
        response = DaemonResponse.ok({"count": 5})
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.data["count"], 5)
        self.assertEqual(response.error, "")

    def test_fail_factory(self):
        response = DaemonResponse.fail("bad action")
        self.assertEqual(response.status, "error")
        self.assertEqual(response.error, "bad action")


if __name__ == "__main__":
    unittest.main()
