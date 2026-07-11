from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse

from ic_env_guard.api.ingest_guard import require_loopback_peer
from ic_env_guard.api.logs import get_log_source_service, log_source_to_dict
from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.logs.models import (
    LogFileUnavailable,
    LogPathForbidden,
    LogSourceConflict,
    LogSourceExpired,
    LogSourceInput,
    LogStorageError,
)
from ic_env_guard.logs.service import LogSourceService

router = APIRouter(
    prefix="/api/v2",
    tags=["local-ingest"],
    dependencies=[Depends(require_loopback_peer)],
)
LogId = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_.-]{0,126}$")]


@router.put("/logs/{log_id}")
def put_log_source(
    log_id: LogId,
    payload: LogSourceInput,
    service: Annotated[LogSourceService, Depends(get_log_source_service)],
) -> JSONResponse:
    now = datetime.now(UTC)
    try:
        result = service.upsert(log_id, payload, now=now)
    except LogPathForbidden as exc:
        raise V2ApiError(403, "log_path_forbidden", "log path is not permitted") from exc
    except LogFileUnavailable as exc:
        raise V2ApiError(422, "log_file_unavailable", "log file is unavailable") from exc
    except LogSourceExpired as exc:
        raise V2ApiError(422, "log_source_expired", "log source is already expired") from exc
    except LogSourceConflict as exc:
        code = str(exc)
        message = (
            "the submitted log source is older than the stored value"
            if code == "stale_log_source"
            else "the submitted timestamp conflicts with the stored value"
        )
        raise V2ApiError(409, code, message) from exc
    except LogStorageError as exc:
        raise V2ApiError(503, "storage_unavailable", "log storage is unavailable") from exc
    except ValueError as exc:
        if str(exc) != "log_source_in_future":
            raise
        raise V2ApiError(
            422, "log_source_in_future", "observed_at is too far in the future"
        ) from exc
    return JSONResponse(
        log_source_to_dict(result.record, now=now),
        status_code=201 if result.created else 200,
    )
