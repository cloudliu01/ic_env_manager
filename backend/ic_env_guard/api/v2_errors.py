import logging
import re
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
logger = logging.getLogger(__name__)


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
    status_code: int, code: str, message: str, correlation_id: str
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "correlation_id": correlation_id,
            }
        },
        headers={"X-Correlation-ID": correlation_id},
    )


async def v2_error_handler(request: Request, exc: V2ApiError) -> JSONResponse:
    return v2_error_response(
        exc.status_code,
        exc.code,
        exc.message,
        request.state.correlation_id,
    )


async def v2_http_exception_handler(request: Request, exc: HTTPException):
    if not is_v2_path(request.url.path):
        return await http_exception_handler(request, exc)
    if exc.status_code == 404:
        code, message = "not_found", "resource not found"
    elif exc.status_code == 405:
        code, message = "method_not_allowed", "method not allowed"
    else:
        code, message = "request_error", "request failed"
    return v2_error_response(
        exc.status_code, code, message, request.state.correlation_id
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
