from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ic_env_guard.auth.dependencies import AuthContext, require_auth
from ic_env_guard.db.control_plane_audit import ControlPlaneAuditRepository

router = APIRouter(prefix="/api/control-plane/audit", tags=["control-plane-audit"])


def get_control_plane_audit_repository() -> Iterator[ControlPlaneAuditRepository]:
    raise RuntimeError("ControlPlaneAuditRepository dependency was not configured")


@router.get("")
def list_control_plane_audit_events(
    _: Annotated[AuthContext, Depends(require_auth)],
    repo: Annotated[
        ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)
    ],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    agent_id: str | None = None,
    operation: str | None = None,
    result: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, list[dict[str, object]]]:
    return {
        "events": repo.list_events(
            limit=limit,
            agent_id=agent_id,
            operation=operation,
            result=result,
            correlation_id=correlation_id,
        )
    }
