from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.agents.models import CapabilityResponse
from ic_env_guard.agents.registry import (
    AgentInvalidConfigurationError,
    AgentNotFoundError,
    AgentRegistry,
)
from ic_env_guard.api.agent_http import (
    ERROR_STATUS,
    augment_upstream_error_body,
    get_agent_http_client,
)
from ic_env_guard.api.audit_health import (
    AuditStorageHealth,
    commit_audit_intent,
    commit_audit_outcome,
    get_audit_storage_health,
)
from ic_env_guard.api.control_plane_audit import get_control_plane_audit_repository
from ic_env_guard.api.errors import ApiError
from ic_env_guard.auth.dependencies import AuthContext, require_auth
from ic_env_guard.db.control_plane_audit import (
    ControlPlaneAuditEventCreate,
    ControlPlaneAuditRepository,
)

local_capabilities_router = APIRouter(prefix="/api", tags=["capabilities"])
control_plane_agents_router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentEnabledRequest(BaseModel):
    enabled: bool


def get_agent_registry() -> AgentRegistry:
    raise RuntimeError("AgentRegistry dependency was not configured")


def get_agent_availability() -> AgentAvailabilityService:
    raise RuntimeError("AgentAvailabilityService dependency was not configured")


async def _proxy_agent_status(
    *,
    agent_id: str,
    upstream_path: str,
    registry: AgentRegistry,
    client: AgentHttpClient,
    request: Request,
    actor: AuthContext,
    audit_repo: ControlPlaneAuditRepository,
    audit_health: AuditStorageHealth,
    operation: str,
) -> Response:
    correlation_id = getattr(request.state, "correlation_id", None)
    source_addr = request.client.host if request.client else None
    try:
        agent = registry.get(agent_id)
    except AgentNotFoundError as exc:
        _record_agent_route_failure(
            audit_repo,
            audit_health,
            actor,
            source_addr,
            agent_id,
            operation,
            correlation_id,
            "agent_not_found",
        )
        raise ApiError(404, "agent_not_found", "agent not found") from exc
    if not agent.enabled:
        _record_agent_route_failure(
            audit_repo,
            audit_health,
            actor,
            source_addr,
            agent_id,
            operation,
            correlation_id,
            "agent_disabled",
        )
        raise ApiError(409, "agent_disabled", "agent is disabled")
    audit = _record_agent_route_intent(
        audit_repo, audit_health, actor, source_addr, agent_id, operation, correlation_id
    )
    try:
        response = await client.request(
            agent,
            "GET",
            upstream_path,
            correlation_id=correlation_id,
        )
    except AgentClientError as exc:
        audit_repo.finalize(
            audit.id,
            result="failed",
            dispatch_state=exc.dispatch_state,
            failure_category=exc.category,
        )
        commit_audit_outcome(audit_repo, audit_health)
        raise ApiError(
            ERROR_STATUS.get(exc.category, 502), exc.category, "agent request failed"
        ) from exc
    audit_repo.finalize(
        audit.id,
        result="success",
        dispatch_state="dispatched",
        upstream_status=response.status_code,
    )
    commit_audit_outcome(audit_repo, audit_health)
    if response.status_code == 204:
        return Response(status_code=204)
    return JSONResponse(
        status_code=response.status_code,
        content=augment_upstream_error_body(
            response.json(),
            agent_id=agent_id,
            correlation_id=correlation_id,
            status_code=response.status_code,
        ),
    )


def _record_agent_route_intent(
    audit_repo: ControlPlaneAuditRepository,
    audit_health: AuditStorageHealth,
    actor: AuthContext,
    source_addr: str | None,
    agent_id: str,
    operation: str,
    correlation_id: str | None,
):
    audit = audit_repo.record_intent(
        ControlPlaneAuditEventCreate(
            actor_id=actor.actor_id,
            source_addr=source_addr,
            agent_id=agent_id,
            operation=operation,
            target=f"agent:{agent_id}",
            correlation_id=correlation_id,
        )
    )
    commit_audit_intent(audit_repo, audit_health)
    return audit


def _record_agent_route_failure(
    audit_repo: ControlPlaneAuditRepository,
    audit_health: AuditStorageHealth,
    actor: AuthContext,
    source_addr: str | None,
    agent_id: str,
    operation: str,
    correlation_id: str | None,
    failure_category: str,
) -> None:
    audit = _record_agent_route_intent(
        audit_repo, audit_health, actor, source_addr, agent_id, operation, correlation_id
    )
    audit_repo.finalize(
        audit.id,
        result="failed",
        dispatch_state="not_dispatched",
        failure_category=failure_category,
    )
    commit_audit_outcome(audit_repo, audit_health)


@local_capabilities_router.get("/capabilities")
def get_capabilities(_: Annotated[AuthContext, Depends(require_auth)]) -> dict[str, object]:
    return CapabilityResponse().to_dict()


@control_plane_agents_router.get("")
def list_agents(
    _: Annotated[AuthContext, Depends(require_auth)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
) -> dict[str, list[dict[str, object]]]:
    return {"agents": availability.list_summaries()}


@control_plane_agents_router.get("/{agent_id}")
def get_agent(
    agent_id: str,
    _: Annotated[AuthContext, Depends(require_auth)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
) -> dict[str, object]:
    try:
        return availability.summary(agent_id)
    except AgentNotFoundError as exc:
        raise ApiError(404, "agent_not_found", "agent not found") from exc


@control_plane_agents_router.post("/{agent_id}/enabled")
def set_agent_enabled(
    agent_id: str,
    body: AgentEnabledRequest,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> dict[str, object]:
    correlation_id = getattr(request.state, "correlation_id", None)
    source_addr = request.client.host if request.client else None
    try:
        registry.set_enabled(agent_id, body.enabled)
    except AgentNotFoundError as exc:
        _record_agent_route_failure(
            audit_repo,
            audit_health,
            actor,
            source_addr,
            agent_id,
            "agents.enabled",
            correlation_id,
            "agent_not_found",
        )
        raise ApiError(404, "agent_not_found", "agent not found") from exc
    except AgentInvalidConfigurationError as exc:
        _record_agent_route_failure(
            audit_repo,
            audit_health,
            actor,
            source_addr,
            agent_id,
            "agents.enabled",
            correlation_id,
            "agent_invalid_configuration",
        )
        raise ApiError(
            409,
            "agent_invalid_configuration",
            "agent cannot be enabled with its current configuration",
        ) from exc
    audit = _record_agent_route_intent(
        audit_repo, audit_health, actor, source_addr, agent_id, "agents.enabled", correlation_id
    )
    availability.clear(agent_id)
    audit_repo.finalize(audit.id, result="success", dispatch_state="not_dispatched")
    commit_audit_outcome(audit_repo, audit_health)
    return {"agent": availability.summary(agent_id)}


@control_plane_agents_router.post("/{agent_id}/probe")
async def probe_agent(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> dict[str, object]:
    correlation_id = getattr(request.state, "correlation_id", None)
    source_addr = request.client.host if request.client else None
    try:
        agent = registry.get(agent_id)
    except AgentNotFoundError as exc:
        _record_agent_route_failure(
            audit_repo,
            audit_health,
            actor,
            source_addr,
            agent_id,
            "agents.probe",
            correlation_id,
            "agent_not_found",
        )
        raise ApiError(404, "agent_not_found", "agent not found") from exc
    if not agent.enabled:
        _record_agent_route_failure(
            audit_repo,
            audit_health,
            actor,
            source_addr,
            agent_id,
            "agents.probe",
            correlation_id,
            "agent_disabled",
        )
        raise ApiError(409, "agent_disabled", "agent is disabled")
    audit = _record_agent_route_intent(
        audit_repo, audit_health, actor, source_addr, agent_id, "agents.probe", correlation_id
    )
    summary = await availability.probe(agent_id)
    audit_repo.finalize(audit.id, result="success", dispatch_state="dispatched")
    commit_audit_outcome(audit_repo, audit_health)
    return summary


@control_plane_agents_router.get("/{agent_id}/healthz")
async def get_agent_healthz(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> Response:
    return await _proxy_agent_status(
        agent_id=agent_id,
        upstream_path="/healthz",
        registry=registry,
        client=client,
        request=request,
        actor=actor,
        audit_repo=audit_repo,
        audit_health=audit_health,
        operation="agents.health",
    )


@control_plane_agents_router.get("/{agent_id}/readyz")
async def get_agent_readyz(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> Response:
    return await _proxy_agent_status(
        agent_id=agent_id,
        upstream_path="/readyz",
        registry=registry,
        client=client,
        request=request,
        actor=actor,
        audit_repo=audit_repo,
        audit_health=audit_health,
        operation="agents.ready",
    )
