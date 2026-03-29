"""Route daemon queries to application use cases."""

from __future__ import annotations

from src.interface.daemon.protocol import DaemonRequest, DaemonResponse


class QueryRouter:
    """Maps action strings to use case execution."""

    def __init__(self, use_cases: dict):
        self._use_cases = use_cases

    def handle(self, request: DaemonRequest) -> DaemonResponse:
        handler = self._use_cases.get(request.action)
        if not handler:
            return DaemonResponse.fail(
                f"Unknown action: {request.action}"
            )
        try:
            result = handler(request.params)
            return DaemonResponse.ok(result)
        except Exception as exc:
            return DaemonResponse.fail(str(exc))
