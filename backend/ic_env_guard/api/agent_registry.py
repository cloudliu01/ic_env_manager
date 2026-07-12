from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from ic_env_guard.agents.registry import (
    AgentInvalidConfigurationError,
    AgentNotFoundError,
    AgentRegistry,
)
from ic_env_guard.api.agents import get_agent_registry
from ic_env_guard.api.audit_health import (
    AuditStorageHealth,
    commit_audit_intent,
    commit_audit_outcome,
    get_audit_storage_health,
)
from ic_env_guard.api.control_plane_audit import get_control_plane_audit_repository
from ic_env_guard.api.runtime import require_v2_auth
from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.auth.dependencies import AuthContext
from ic_env_guard.db.control_plane_audit import (
    ControlPlaneAuditEventCreate,
    ControlPlaneAuditRepository,
)
from ic_env_guard.fleet.probes import AgentProbeDisabled, AgentProbeError, FleetProbeService
from ic_env_guard.fleet.status import FleetStatusService, InvalidFleetCursor

router = APIRouter(prefix="/api/v2/agents", tags=["agent-registry-v2"])


class AgentEnabledRequest(BaseModel):
    enabled: bool


def get_fleet_status_service() -> FleetStatusService:
    raise RuntimeError("FleetStatusService dependency was not configured")


def get_fleet_probe_service() -> FleetProbeService:
    raise RuntimeError("FleetProbeService dependency was not configured")


@router.get("")
def list_agents(
    _: Annotated[AuthContext, Depends(require_v2_auth)],
    service: Annotated[FleetStatusService, Depends(get_fleet_status_service)],
    query: str | None = Query(default=None, max_length=256),
    connection_status: Literal[
        "disabled", "unknown", "ready", "degraded", "unavailable"
    ]
    | None = None,
    workload_status: Literal["unknown", "healthy", "warning", "critical", "stale"]
    | None = None,
    capability: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=100, ge=1, le=1000),
    cursor: str | None = Query(default=None, max_length=512),
) -> dict[str, object]:
    try:
        page = service.list(
            query=query,
            connection_status=connection_status,
            workload_status=workload_status,
            capability=capability,
            limit=limit,
            cursor=cursor,
        )
    except InvalidFleetCursor as exc:
        raise V2ApiError(422, "invalid_cursor", "cursor is invalid") from exc
    return {"agents": list(page.agents), "next_cursor": page.next_cursor}


@router.get("/{agent_id}")
def get_agent(
    agent_id: str,
    _: Annotated[AuthContext, Depends(require_v2_auth)],
    service: Annotated[FleetStatusService, Depends(get_fleet_status_service)],
) -> dict[str, object]:
    agent = service.get(agent_id)
    if agent is None:
        raise V2ApiError(404, "agent_not_found", "agent not found")
    return {"agent": agent}


@router.post("/{agent_id}/enabled")
def set_enabled(
    agent_id: str,
    body: AgentEnabledRequest,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    service: Annotated[FleetStatusService, Depends(get_fleet_status_service)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> dict[str, object]:
    audit = _intent(
        request, actor, audit_repo, audit_health, agent_id, "agents.v2.enabled"
    )
    try:
        registry.set_enabled(agent_id, body.enabled)
    except AgentNotFoundError as exc:
        _failure(audit, audit_repo, audit_health, "agent_not_found", dispatched=False)
        raise V2ApiError(404, "agent_not_found", "agent not found") from exc
    except AgentInvalidConfigurationError as exc:
        _failure(
            audit,
            audit_repo,
            audit_health,
            "agent_invalid_configuration",
            dispatched=False,
        )
        raise V2ApiError(
            409,
            "agent_invalid_configuration",
            "agent cannot be enabled with its current configuration",
        ) from exc
    _success(audit, audit_repo, audit_health, dispatched=False)
    agent = service.get(agent_id)
    assert agent is not None
    return {"agent": agent}


@router.post("/{agent_id}/probe")
async def probe(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    probe_service: Annotated[FleetProbeService, Depends(get_fleet_probe_service)],
    status_service: Annotated[FleetStatusService, Depends(get_fleet_status_service)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> dict[str, object]:
    audit = _intent(request, actor, audit_repo, audit_health, agent_id, "agents.v2.probe")
    current = status_service.get(agent_id)
    if current is None:
        _failure(audit, audit_repo, audit_health, "agent_not_found", dispatched=False)
        raise V2ApiError(404, "agent_not_found", "agent not found")
    if not current["enabled"]:
        _failure(audit, audit_repo, audit_health, "agent_disabled", dispatched=False)
        raise V2ApiError(409, "agent_disabled", "agent is disabled")
    try:
        result = await probe_service.probe(agent_id)
    except AgentProbeDisabled as exc:
        _failure(audit, audit_repo, audit_health, exc.code, dispatched=False)
        raise V2ApiError(409, exc.code, "agent is disabled") from exc
    except AgentProbeError as exc:
        _failure(audit, audit_repo, audit_health, exc.code, dispatched=True)
        status_code = 404 if exc.code == "agent_not_found" else 409
        raise V2ApiError(status_code, exc.code, "agent probe failed") from exc
    if result.connection_status == "unavailable":
        _failure(
            audit,
            audit_repo,
            audit_health,
            result.last_error_code or "agent_unavailable",
            dispatched=True,
        )
    else:
        _success(audit, audit_repo, audit_health, dispatched=True)
    agent = status_service.get(agent_id)
    assert agent is not None
    return {"agent": agent}


def _intent(
    request: Request,
    actor: AuthContext,
    repository: ControlPlaneAuditRepository,
    health: AuditStorageHealth,
    agent_id: str,
    operation: str,
):
    try:
        event = repository.record_intent(
            ControlPlaneAuditEventCreate(
                actor_id=actor.actor_id,
                source_addr=request.client.host if request.client else None,
                agent_id=agent_id,
                operation=operation,
                target=f"agent:{agent_id}",
                correlation_id=getattr(request.state, "correlation_id", None),
            )
        )
        commit_audit_intent(repository, health)
    except Exception as exc:
        session = getattr(repository, "session", None)
        if session is not None:
            session.rollback()
        health.mark_unhealthy()
        raise V2ApiError(503, "audit_unavailable", "audit storage is unavailable") from exc
    return event


def _failure(event, repository, health, code: str, *, dispatched: bool) -> None:
    repository.finalize(
        event.id,
        result="failed",
        dispatch_state="dispatched" if dispatched else "not_dispatched",
        failure_category=code,
    )
    commit_audit_outcome(repository, health)


def _success(event, repository, health, *, dispatched: bool) -> None:
    repository.finalize(
        event.id,
        result="success",
        dispatch_state="dispatched" if dispatched else "not_dispatched",
    )
    commit_audit_outcome(repository, health)
