from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.auth.dependencies import AuthContext, require_auth
from ic_env_guard.logs.models import LogStorageError
from ic_env_guard.observations.models import ObservationStorageError
from ic_env_guard.summary.service import SummaryService

router = APIRouter(prefix="/api/v2", tags=["summary"])


def get_summary_service() -> SummaryService:
    raise RuntimeError("SummaryService dependency was not configured")


@router.get("/summary")
def summary(
    _: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[SummaryService, Depends(get_summary_service)],
) -> JSONResponse:
    try:
        result = service.get(now=datetime.now(UTC))
    except (ObservationStorageError, LogStorageError) as exc:
        raise V2ApiError(503, "storage_unavailable", "summary storage is unavailable") from exc
    return JSONResponse(result.to_dict())
