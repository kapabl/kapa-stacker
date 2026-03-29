"""Daemon JSON protocol — request/response schema."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict


SOCKET_PATH = "/tmp/kapa-cortex.sock"
HEADER_SIZE = 8  # 8 bytes for message length prefix


@dataclass(frozen=True)
class DaemonRequest:
    """Client → daemon query."""

    action: str
    params: dict = field(default_factory=dict)

    def serialize(self) -> bytes:
        payload = json.dumps(asdict(self)).encode("utf-8")
        header = len(payload).to_bytes(HEADER_SIZE, "big")
        return header + payload

    @classmethod
    def deserialize(cls, data: bytes) -> DaemonRequest:
        parsed = json.loads(data.decode("utf-8"))
        return cls(
            action=parsed.get("action", ""),
            params=parsed.get("params", {}),
        )


@dataclass
class DaemonResponse:
    """Daemon → client response."""

    status: str
    data: dict = field(default_factory=dict)
    error: str = ""

    def serialize(self) -> bytes:
        payload = json.dumps(asdict(self)).encode("utf-8")
        header = len(payload).to_bytes(HEADER_SIZE, "big")
        return header + payload

    @classmethod
    def deserialize(cls, data: bytes) -> DaemonResponse:
        parsed = json.loads(data.decode("utf-8"))
        return cls(
            status=parsed.get("status", "error"),
            data=parsed.get("data", {}),
            error=parsed.get("error", ""),
        )

    @classmethod
    def ok(cls, data: dict) -> DaemonResponse:
        return cls(status="ok", data=data)

    @classmethod
    def fail(cls, error: str) -> DaemonResponse:
        return cls(status="error", error=error)
