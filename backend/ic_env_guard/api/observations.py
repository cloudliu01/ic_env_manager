import base64
import binascii
import json
import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.auth.dependencies import AuthContext, require_auth
from ic_env_guard.observations.models import (
    Observation,
    ObservationQuery,
    ObservationStatus,
    ObservationStorageError,
)
from ic_env_guard.observations.service import ObservationService

router = APIRouter(prefix="/api/v2", tags=["observations"])
_IDENTITY_KEY = re.compile(r"^[0-9a-f]{64}$")
_MAX_CURSOR_LENGTH = 512


def get_observation_service() -> ObservationService:
    raise RuntimeError("ObservationService dependency was not configured")


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def observation_to_dict(record: Observation, *, now: datetime) -> dict[str, object]:
    return {
        "identity_key": record.identity_key,
        "namespace": record.namespace,
        "name": record.name,
        "kind": record.kind,
        "value": record.value,
        "unit": record.unit,
        "status": record.status,
        "message": record.message,
        "labels": record.labels,
        "details": record.details,
        "observed_at": _format_time(record.observed_at),
        "ttl_seconds": record.ttl_seconds,
        "received_at": _format_time(record.received_at),
        "expires_at": _format_time(record.expires_at),
        "producer_id": record.producer_id,
        "updated_at": _format_time(record.updated_at),
        "stale": record.is_stale(now),
    }


def _encode_cursor(identity_key: str | None) -> str | None:
    if identity_key is None:
        return None
    payload = json.dumps(
        {"v": 1, "sort": [identity_key]}, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> str | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > _MAX_CURSOR_LENGTH:
        raise V2ApiError(422, "invalid_cursor", "cursor is malformed or unsupported")
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        value = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        raise V2ApiError(
            422, "invalid_cursor", "cursor is malformed or unsupported"
        ) from None
    if (
        not isinstance(value, dict)
        or set(value) != {"v", "sort"}
        or type(value["v"]) is not int
        or value["v"] != 1
        or not isinstance(value["sort"], list)
        or len(value["sort"]) != 1
        or not isinstance(value["sort"][0], str)
        or not _IDENTITY_KEY.fullmatch(value["sort"][0])
    ):
        raise V2ApiError(422, "invalid_cursor", "cursor is malformed or unsupported")
    return value["sort"][0]


def _storage_error(exc: ObservationStorageError) -> V2ApiError:
    return V2ApiError(503, "storage_unavailable", "observation storage is unavailable")


@router.get("/observations")
def list_observations(
    _: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[ObservationService, Depends(get_observation_service)],
    namespace: str | None = None,
    name: str | None = None,
    status: ObservationStatus | None = None,
    include_stale: bool = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    cursor: str | None = None,
) -> JSONResponse:
    now = datetime.now(UTC)
    try:
        page = service.list(
            ObservationQuery(
                namespace=namespace,
                name=name,
                status=status,
                include_stale=include_stale,
                limit=limit,
                cursor=_decode_cursor(cursor),
            ),
            now=now,
        )
    except ObservationStorageError as exc:
        raise _storage_error(exc) from exc
    return JSONResponse(
        {
            "items": [observation_to_dict(record, now=now) for record in page.items],
            "next_cursor": _encode_cursor(page.next_cursor),
        }
    )


@router.get("/observations/{identity_key}")
def get_observation(
    identity_key: str,
    _: Annotated[AuthContext, Depends(require_auth)],
    service: Annotated[ObservationService, Depends(get_observation_service)],
    include_stale: bool = False,
) -> JSONResponse:
    now = datetime.now(UTC)
    try:
        record = service.get(identity_key, now=now, include_stale=include_stale)
    except ObservationStorageError as exc:
        raise _storage_error(exc) from exc
    if record is None:
        raise V2ApiError(404, "observation_not_found", "observation was not found")
    return JSONResponse(observation_to_dict(record, now=now))
