import logging
import re
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse

_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
logger = logging.getLogger(__name__)


class V2ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def resolve_v2_correlation_id(submitted: str | None) -> str:
    if submitted is not None and _CORRELATION_ID.fullmatch(submitted):
        return submitted
    return str(uuid4())


def _response(status_code: int, code: str, message: str, correlation_id: str) -> JSONResponse:
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
    return _response(
        exc.status_code,
        exc.code,
        exc.message,
        request.state.correlation_id,
    )


def unexpected_v2_error_response(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unexpected v2 API error correlation_id=%s",
        request.state.correlation_id,
        exc_info=exc,
    )
    return _response(
        500,
        "internal_error",
        "an unexpected error occurred",
        request.state.correlation_id,
    )
