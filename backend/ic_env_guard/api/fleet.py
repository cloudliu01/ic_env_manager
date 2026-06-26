from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.api.agents import get_agent_availability
from ic_env_guard.api.audit_health import (
    AuditStorageHealth,
    commit_audit_intent,
    commit_audit_outcome,
    get_audit_storage_health,
)
from ic_env_guard.api.control_plane_audit import get_control_plane_audit_repository
from ic_env_guard.auth.dependencies import AuthContext, require_auth
from ic_env_guard.db.control_plane_audit import (
    ControlPlaneAuditEventCreate,
    ControlPlaneAuditRepository,
)

router = APIRouter(prefix="/api/fleet", tags=["fleet"])


@router.get("/overview")
def get_fleet_overview(
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> dict[str, object]:
    correlation_id = getattr(request.state, "correlation_id", None)
    source_addr = request.client.host if request.client else None
    audit = audit_repo.record_intent(
        ControlPlaneAuditEventCreate(
            actor_id=actor.actor_id,
            source_addr=source_addr,
            agent_id=None,
            operation="fleet.overview",
            target="fleet:overview",
            correlation_id=correlation_id,
        )
    )
    commit_audit_intent(audit_repo, audit_health)
    audit_repo.finalize(audit.id, result="success", dispatch_state="not_dispatched")
    commit_audit_outcome(audit_repo, audit_health)
    return {
        "hosts": availability.list_summaries(),
        "collected_at": datetime.now(UTC).isoformat(),
    }
