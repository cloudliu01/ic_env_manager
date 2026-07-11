from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ic_env_guard.api.ingest_guard import require_loopback_peer
from ic_env_guard.api.observations import get_observation_service, observation_to_dict
from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.observations.models import (
    ObservationConflict,
    ObservationExpired,
    ObservationInput,
    ObservationSeriesLimit,
    ObservationStorageError,
)
from ic_env_guard.observations.service import ObservationService

router = APIRouter(
    prefix="/api/v2",
    tags=["local-ingest"],
    dependencies=[Depends(require_loopback_peer)],
)


@router.put("/observations")
def put_observation(
    payload: ObservationInput,
    service: Annotated[ObservationService, Depends(get_observation_service)],
) -> JSONResponse:
    now = datetime.now(UTC)
    try:
        result = service.upsert(payload, now=now)
    except ObservationExpired as exc:
        raise V2ApiError(422, "observation_expired", "observation is already expired") from exc
    except ObservationConflict as exc:
        code = str(exc)
        message = (
            "the submitted observation is older than the stored value"
            if code == "stale_observation"
            else "the submitted timestamp conflicts with the stored value"
        )
        raise V2ApiError(409, code, message) from exc
    except ObservationSeriesLimit as exc:
        raise V2ApiError(
            409,
            "observation_series_limit",
            "observation series capacity has been reached",
        ) from exc
    except ObservationStorageError as exc:
        raise V2ApiError(
            503, "storage_unavailable", "observation storage is unavailable"
        ) from exc
    except ValueError as exc:
        if str(exc) != "observation_in_future":
            raise
        raise V2ApiError(
            422, "observation_in_future", "observed_at is too far in the future"
        ) from exc
    return JSONResponse(
        observation_to_dict(result.record, now=now),
        status_code=201 if result.created else 200,
    )
