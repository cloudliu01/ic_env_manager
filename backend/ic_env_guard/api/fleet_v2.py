from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from ic_env_guard.api.agent_registry import (
    get_fleet_status_service,
)
from ic_env_guard.api.runtime import require_v2_auth
from ic_env_guard.auth.dependencies import AuthContext
from ic_env_guard.fleet.status import FleetStatusService

router = APIRouter(prefix="/api/v2/fleet", tags=["fleet-v2"])


@router.get("/overview")
def overview(
    _: Annotated[AuthContext, Depends(require_v2_auth)],
    service: Annotated[FleetStatusService, Depends(get_fleet_status_service)],
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "collected_at": now.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "agents": list(service.overview(now=now)),
    }
