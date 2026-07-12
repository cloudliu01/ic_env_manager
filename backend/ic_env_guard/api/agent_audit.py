from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.agents.registry import AgentNotFoundError, AgentRegistry
from ic_env_guard.api.agent_http import (
    ERROR_STATUS,
    augment_upstream_error_body,
    get_agent_http_client,
)
from ic_env_guard.api.agents import get_agent_availability, get_agent_registry
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

router = APIRouter(prefix="/api/agents/{agent_id}/audit", tags=["agent-audit"])


@router.get("")
async def list_agent_audit_events(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    target_type: str | None = None,
    result: str | None = None,
) -> Response:
    correlation_id = getattr(request.state, "correlation_id", None) or str(uuid4())
    source_addr = request.client.host if request.client else None
    try:
        agent = registry.get(agent_id)
    except AgentNotFoundError as exc:
        _record_failure(
            audit_repo,
            actor,
            agent_id,
            correlation_id,
            "agent_not_found",
            audit_health,
            source_addr,
        )
        raise ApiError(404, "agent_not_found", "agent not found") from exc
    if not agent.enabled:
        _record_failure(
            audit_repo,
            actor,
            agent_id,
            correlation_id,
            "agent_disabled",
            audit_health,
            source_addr,
        )
        raise ApiError(409, "agent_disabled", "agent is disabled")
    if not await availability.ensure_capability(agent_id, "audit.v1"):
        _record_failure(
            audit_repo,
            actor,
            agent_id,
            correlation_id,
            "missing_capability",
            audit_health,
            source_addr,
        )
        raise ApiError(409, "agent_capability_missing", "agent does not support audit")

    params: dict[str, str | int] = {"limit": limit}
    if target_type is not None:
        params["target_type"] = target_type
    if result is not None:
        params["result"] = result

    gateway_audit = audit_repo.record_intent(
        ControlPlaneAuditEventCreate(
            actor_id=actor.actor_id,
            source_addr=source_addr,
            agent_id=agent_id,
            operation="audit.list",
            target="audit:events",
            correlation_id=correlation_id,
        )
    )
    commit_audit_intent(audit_repo, audit_health)
    try:
        response = await client.request(
            agent, "GET", "/api/audit", params=params, correlation_id=correlation_id
        )
    except AgentClientError as exc:
        audit_repo.finalize(
            gateway_audit.id,
            result="failed",
            dispatch_state=exc.dispatch_state,
            failure_category=exc.category,
        )
        commit_audit_outcome(audit_repo, audit_health)
        raise ApiError(
            ERROR_STATUS.get(exc.category, 502), exc.category, "agent request failed"
        ) from exc

    audit_repo.finalize(
        gateway_audit.id,
        result="success" if response.status_code < 400 else "failed",
        dispatch_state="dispatched",
        upstream_status=response.status_code,
    )
    commit_audit_outcome(audit_repo, audit_health)
    body = response.json()
    events = body.get("events", [])
    if isinstance(events, list):
        body = {**body, "events": [{**event, "agent_id": agent_id} for event in events]}
    body = augment_upstream_error_body(
        body,
        agent_id=agent_id,
        correlation_id=correlation_id,
        status_code=response.status_code,
    )
    return JSONResponse(status_code=response.status_code, content=body)


def _record_failure(
    audit_repo: ControlPlaneAuditRepository,
    actor: AuthContext,
    agent_id: str,
    correlation_id: str,
    failure_category: str,
    audit_health: AuditStorageHealth,
    source_addr: str | None = None,
) -> None:
    audit = audit_repo.record_intent(
        ControlPlaneAuditEventCreate(
            actor_id=actor.actor_id,
            source_addr=source_addr,
            agent_id=agent_id,
            operation="audit.list",
            target="audit:events",
            correlation_id=correlation_id,
        )
    )
    audit_repo.finalize(
        audit.id,
        result="failed",
        dispatch_state="not_dispatched",
        failure_category=failure_category,
    )
    commit_audit_outcome(audit_repo, audit_health)
