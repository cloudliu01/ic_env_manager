from __future__ import annotations

from ipaddress import ip_address
from threading import Lock
from typing import Any

from fastapi import Request

from ic_env_guard.api.v2_errors import V2ApiError, resolve_v2_correlation_id, v2_error_response


def require_loopback_peer(request: Request) -> None:
    host = request.client.host if request.client is not None else ""
    try:
        loopback = ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise V2ApiError(403, "ingest_peer_forbidden", "ingest requires a loopback peer")
    if any(
        name in request.headers
        for name in ("producer-id", "producer_id", "x-producer-id")
    ):
        raise V2ApiError(
            422,
            "validation_error",
            "producer_id is assigned by the agent",
        )


class IngestCapacityMiddleware:
    def __init__(self, app: Any, *, maximum: int, max_request_bytes: int) -> None:
        self.app = app
        self.maximum = maximum
        self.max_request_bytes = max_request_bytes
        self._active = 0
        self._lock = Lock()

    @staticmethod
    def _header(scope: dict[str, Any], name: bytes) -> str | None:
        for key, value in scope.get("headers", ()):
            if key.lower() == name:
                return value.decode("latin-1")
        return None

    async def _error(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
        status_code: int,
        code: str,
        message: str,
    ) -> None:
        correlation_id = resolve_v2_correlation_id(
            self._header(scope, b"x-correlation-id")
        )
        response = v2_error_response(status_code, code, message, correlation_id)
        await response(scope, receive, send)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        with self._lock:
            if self._active >= self.maximum:
                admitted = False
            else:
                self._active += 1
                admitted = True
        if not admitted:
            await self._error(
                scope,
                receive,
                send,
                503,
                "ingest_capacity_exceeded",
                "ingest request capacity is exhausted",
            )
            return

        try:
            content_length = self._header(scope, b"content-length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    declared_length = 0
                if declared_length > self.max_request_bytes:
                    await self._error(
                        scope,
                        receive,
                        send,
                        413,
                        "request_too_large",
                        "request body exceeds the ingest limit",
                    )
                    return

            messages: list[dict[str, Any]] = []
            total = 0
            while True:
                message = await receive()
                messages.append(message)
                if message["type"] == "http.disconnect":
                    return
                total += len(message.get("body", b""))
                if total > self.max_request_bytes:
                    await self._error(
                        scope,
                        receive,
                        send,
                        413,
                        "request_too_large",
                        "request body exceeds the ingest limit",
                    )
                    return
                if not message.get("more_body", False):
                    break

            index = 0

            async def replay() -> dict[str, Any]:
                nonlocal index
                if index < len(messages):
                    message = messages[index]
                    index += 1
                    return message
                return {"type": "http.request", "body": b"", "more_body": False}

            await self.app(scope, replay, send)
        finally:
            with self._lock:
                self._active -= 1
