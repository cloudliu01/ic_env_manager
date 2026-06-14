from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ic_env_guard.api.audit_health import AuditStorageUnavailable


class ApiError(Exception):
    def __init__(self, status_code: int, error: str, message: str) -> None:
        self.status_code = status_code
        self.error = error
        self.message = message


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", None)
    content = {"error": exc.error, "message": exc.message}
    if correlation_id is not None:
        content["correlation_id"] = correlation_id
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers={"X-Correlation-ID": correlation_id} if correlation_id else None,
    )


async def audit_storage_error_handler(
    request: Request, exc: AuditStorageUnavailable
) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", None)
    content = {"error": "audit_storage_unavailable", "message": str(exc)}
    if correlation_id is not None:
        content["correlation_id"] = correlation_id
    return JSONResponse(
        status_code=503,
        content=content,
        headers={"X-Correlation-ID": correlation_id} if correlation_id else None,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(AuditStorageUnavailable, audit_storage_error_handler)
