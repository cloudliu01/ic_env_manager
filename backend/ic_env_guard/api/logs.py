from datetime import UTC, datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from ic_env_guard.api.audit_health import AuditStorageHealth, AuditStorageUnavailable
from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.auth.dependencies import AuthContext, require_auth
from ic_env_guard.config.models import LogsConfig
from ic_env_guard.db.audit import AuditEventCreate, AuditRepository
from ic_env_guard.logs.models import (
    LogFileUnavailable,
    LogPathForbidden,
    LogSource,
    LogSourceExpired,
    LogStorageError,
)
from ic_env_guard.logs.service import LogSourceService

router = APIRouter(prefix="/api/v2", tags=["logs"])
LogId = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_.-]{0,126}$")]


class LogTailAuditRecorder(Protocol):
    def record(
        self,
        *,
        actor_id: str,
        log_id: str,
        requested_lines: int,
        result: str,
        source_addr: str | None,
        correlation_id: str | None,
    ) -> None: ...


class AgentLogTailAuditRecorder:
    def __init__(
        self,
        session_factory: sessionmaker,
        storage_health: AuditStorageHealth,
    ) -> None:
        self._session_factory = session_factory
        self._storage_health = storage_health

    def record(
        self,
        *,
        actor_id: str,
        log_id: str,
        requested_lines: int,
        result: str,
        source_addr: str | None,
        correlation_id: str | None,
    ) -> None:
        if result == "success":
            audit_result = "success"
        elif result == "log_path_forbidden":
            audit_result = "denied"
        elif result in {"log_source_not_found", "log_source_stale"}:
            audit_result = "rejected"
        else:
            audit_result = "failed"
        try:
            with self._session_factory() as session:
                AuditRepository(session).add(
                    AuditEventCreate(
                        actor_id=actor_id,
                        source_addr=source_addr,
                        operation="logs.tail",
                        target_type="log",
                        target_id=log_id,
                        result=audit_result,
                        failure_reason=f"lines={requested_lines};result={result}",
                        correlation_id=correlation_id,
                    )
                )
                session.commit()
        except Exception as exc:
            self._storage_health.mark_unhealthy()
            raise AuditStorageUnavailable("audit storage is unavailable") from exc
        self._storage_health.mark_healthy()


def get_log_source_service() -> LogSourceService:
    raise RuntimeError("LogSourceService dependency was not configured")


def get_log_tail_audit_recorder() -> LogTailAuditRecorder:
    raise RuntimeError("LogTailAuditRecorder dependency was not configured")


def get_logs_config() -> LogsConfig:
    raise RuntimeError("LogsConfig dependency was not configured")


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def log_source_to_dict(record: LogSource, *, now: datetime) -> dict[str, object]:
    return {
        "id": record.id,
        "path": str(record.path),
        "last_updated": _format_time(record.last_updated),
        "observed_at": _format_time(record.observed_at),
        "ttl_seconds": record.ttl_seconds,
        "received_at": _format_time(record.received_at),
        "expires_at": _format_time(record.expires_at),
        "producer_id": record.producer_id,
        "updated_at": _format_time(record.updated_at),
        "stale": record.is_stale(now),
    }


def _storage_error(exc: LogStorageError) -> V2ApiError:
    return V2ApiError(503, "storage_unavailable", "log storage is unavailable")


@router.get("/logs")
def list_log_sources(
    _: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[LogSourceService, Depends(get_log_source_service)],
) -> dict[str, list[dict[str, object]]]:
    now = datetime.now(UTC)
    try:
        records = service.list(now=now)
    except LogStorageError as exc:
        raise _storage_error(exc) from exc
    return {"items": [log_source_to_dict(record, now=now) for record in records]}


@router.get("/logs/{log_id}")
def get_log_source(
    log_id: LogId,
    _: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[LogSourceService, Depends(get_log_source_service)],
) -> dict[str, object]:
    now = datetime.now(UTC)
    try:
        record = service.get(log_id, now=now)
    except LogStorageError as exc:
        raise _storage_error(exc) from exc
    if record is None:
        raise V2ApiError(404, "log_source_not_found", "log source was not found")
    return log_source_to_dict(record, now=now)


@router.get("/logs/{log_id}/tail")
def tail_log_source(
    request: Request,
    log_id: LogId,
    actor: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[LogSourceService, Depends(get_log_source_service)],
    audit: Annotated[LogTailAuditRecorder, Depends(get_log_tail_audit_recorder)],
    config: Annotated[LogsConfig, Depends(get_logs_config)],
    lines: Annotated[int | None, Query(ge=1, le=1000)] = None,
) -> JSONResponse:
    now = datetime.now(UTC)
    requested_lines = config.default_tail_lines if lines is None else lines
    if requested_lines > config.max_tail_lines:
        raise V2ApiError(422, "validation_error", "request validation failed")
    source_addr = request.client.host if request.client else None
    correlation_id = getattr(request.state, "correlation_id", None)

    def audited(result: str) -> None:
        try:
            audit.record(
                actor_id=actor.actor_id,
                log_id=log_id,
                requested_lines=requested_lines,
                result=result,
                source_addr=source_addr,
                correlation_id=correlation_id,
            )
        except AuditStorageUnavailable:
            raise
        except Exception as exc:
            raise AuditStorageUnavailable("audit storage is unavailable") from exc

    try:
        record = service.get(log_id, now=now, include_stale=True)
    except LogStorageError as exc:
        audited("storage_unavailable")
        raise _storage_error(exc) from exc
    if record is None:
        audited("log_source_not_found")
        raise V2ApiError(404, "log_source_not_found", "log source was not found")
    if record.is_stale(now):
        audited("log_source_stale")
        raise V2ApiError(410, "log_source_stale", "log source is stale")

    try:
        result = service.tail(log_id, lines=requested_lines, now=now)
    except LogPathForbidden as exc:
        audited("log_path_forbidden")
        raise V2ApiError(403, "log_path_forbidden", "log path is not permitted") from exc
    except LogFileUnavailable as exc:
        audited("log_file_unavailable")
        raise V2ApiError(410, "log_file_unavailable", "log file is unavailable") from exc
    except LogSourceExpired as exc:
        audited("log_source_stale")
        raise V2ApiError(410, "log_source_stale", "log source is stale") from exc
    except LogStorageError as exc:
        audited("storage_unavailable")
        raise _storage_error(exc) from exc

    audited("success")
    body = {
        "id": record.id,
        "path": str(record.path),
        "lines": list(result.lines),
        "line_count": len(result.lines),
        "truncated": result.truncated,
        "last_updated": _format_time(record.last_updated),
    }
    return JSONResponse(body)
