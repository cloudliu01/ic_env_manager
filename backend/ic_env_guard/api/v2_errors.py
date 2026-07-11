import logging
import re
from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from itertools import islice
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.routing import Match

_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
logger = logging.getLogger(__name__)
_MAX_ROUTE_NODES = 512
_MAX_ROUTE_DEPTH = 16
_MAX_ALLOWED_METHODS = 32


class V2ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def is_v2_path(path: str) -> bool:
    return path == "/api/v2" or path.startswith("/api/v2/")


def resolve_v2_correlation_id(submitted: str | None) -> str:
    if submitted is not None and _CORRELATION_ID.fullmatch(submitted):
        return submitted
    return str(uuid4())


def v2_error_response(
    status_code: int,
    code: str,
    message: str,
    correlation_id: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    response_headers = {"X-Correlation-ID": correlation_id}
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "correlation_id": correlation_id,
            }
        },
        headers=response_headers,
    )


async def v2_error_handler(request: Request, exc: V2ApiError) -> JSONResponse:
    return v2_error_response(
        exc.status_code,
        exc.code,
        exc.message,
        request.state.correlation_id,
    )


def _iter_routes(value: Any) -> Iterator[Any]:
    if value is None or isinstance(value, (str, bytes)):
        return
    try:
        routes: Iterable[Any] = iter(value)
    except TypeError:
        return
    for route in islice(routes, _MAX_ROUTE_NODES):
        if callable(getattr(route, "matches", None)):
            yield route


def _child_routes(route: Any) -> Iterator[Any]:
    yield from _iter_routes(getattr(route, "routes", None))
    for attribute in ("router", "original_router"):
        container = getattr(route, attribute, None)
        yield from _iter_routes(getattr(container, "routes", None))


def _allowed_methods(request: Request) -> set[str]:
    queue = deque(
        (route, 0)
        for route in _iter_routes(getattr(request.app.router, "routes", None))
    )
    seen: set[int] = set()
    methods: set[str] = set()
    visited = 0
    while queue and visited < _MAX_ROUTE_NODES:
        route, depth = queue.popleft()
        identity = id(route)
        if identity in seen:
            continue
        seen.add(identity)
        visited += 1
        match, _ = route.matches(request.scope)
        if match is Match.PARTIAL:
            route_methods = getattr(route, "methods", ()) or ()
            for method in route_methods:
                if isinstance(method, str) and len(methods) < _MAX_ALLOWED_METHODS:
                    methods.add(method)
        if depth >= _MAX_ROUTE_DEPTH:
            continue
        for child in _child_routes(route):
            if visited + len(queue) >= _MAX_ROUTE_NODES:
                break
            queue.append((child, depth + 1))
    return methods


async def v2_http_exception_handler(request: Request, exc: HTTPException):
    if not is_v2_path(request.url.path):
        return await http_exception_handler(request, exc)
    if exc.status_code == 404:
        code, message = "not_found", "resource not found"
    elif exc.status_code == 405:
        code, message = "method_not_allowed", "method not allowed"
    else:
        code, message = "request_error", "request failed"
    headers: dict[str, str] = {}
    allow = None
    if exc.headers:
        allow = next(
            (value for key, value in exc.headers.items() if key.lower() == "allow"), None
        )
    if allow is None and exc.status_code == 405:
        allow = ", ".join(sorted(_allowed_methods(request))) or None
    if allow is not None and len(allow) <= 128 and re.fullmatch(
        r"[A-Z]+(?:, ?[A-Z]+)*", allow
    ):
        headers["Allow"] = allow
    return v2_error_response(
        exc.status_code,
        code,
        message,
        request.state.correlation_id,
        headers=headers,
    )


async def v2_request_validation_handler(
    request: Request, exc: RequestValidationError
):
    if not is_v2_path(request.url.path):
        return await request_validation_exception_handler(request, exc)
    return v2_error_response(
        422,
        "validation_error",
        "request validation failed",
        request.state.correlation_id,
    )


def unexpected_v2_error_response(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unexpected v2 API error correlation_id=%s",
        request.state.correlation_id,
        exc_info=exc,
    )
    return v2_error_response(
        500,
        "internal_error",
        "an unexpected error occurred",
        request.state.correlation_id,
    )


def register_v2_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(V2ApiError, v2_error_handler)
    app.add_exception_handler(HTTPException, v2_http_exception_handler)
    app.add_exception_handler(RequestValidationError, v2_request_validation_handler)
