from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.agents.registry import AgentNotFoundError, AgentRegistry
from ic_env_guard.api.agent_http import (
    ERROR_STATUS,
    augment_upstream_error_body,
    failure_category_for_client_error,
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

router = APIRouter(prefix="/api/agents/{agent_id}/services", tags=["agent-services"])


def _correlation_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) else str(uuid4())


def _source_addr(request: Request) -> str | None:
    return request.client.host if request.client else None


def _record_pre_dispatch_failure(
    *,
    audit_repo: ControlPlaneAuditRepository,
    audit_health: AuditStorageHealth,
    actor: AuthContext,
    agent_id: str,
    operation: str,
    target: str,
    correlation_id: str,
    source_addr: str | None,
    failure_category: str,
) -> None:
    audit = audit_repo.record_intent(
        ControlPlaneAuditEventCreate(
            actor_id=actor.actor_id,
            source_addr=source_addr,
            agent_id=agent_id,
            operation=operation,
            target=target,
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


async def _dispatch(
    *,
    agent_id: str,
    upstream_path: str,
    method: str,
    operation: str,
    target: str,
    actor: AuthContext,
    registry: AgentRegistry,
    availability: AgentAvailabilityService,
    client: AgentHttpClient,
    audit_repo: ControlPlaneAuditRepository,
    audit_health: AuditStorageHealth,
    correlation_id: str,
    source_addr: str | None,
) -> Response:
    try:
        agent = registry.get(agent_id)
    except AgentNotFoundError as exc:
        _record_pre_dispatch_failure(
            audit_repo=audit_repo,
            audit_health=audit_health,
            actor=actor,
            agent_id=agent_id,
            operation=operation,
            target=target,
            correlation_id=correlation_id,
            source_addr=source_addr,
            failure_category="agent_not_found",
        )
        raise ApiError(404, "agent_not_found", "agent not found") from exc
    if not agent.enabled:
        _record_pre_dispatch_failure(
            audit_repo=audit_repo,
            audit_health=audit_health,
            actor=actor,
            agent_id=agent_id,
            operation=operation,
            target=target,
            correlation_id=correlation_id,
            source_addr=source_addr,
            failure_category="agent_disabled",
        )
        raise ApiError(409, "agent_disabled", "agent is disabled")
    if not await availability.ensure_capability(agent_id, "services.v1"):
        _record_pre_dispatch_failure(
            audit_repo=audit_repo,
            audit_health=audit_health,
            actor=actor,
            agent_id=agent_id,
            operation=operation,
            target=target,
            correlation_id=correlation_id,
            source_addr=source_addr,
            failure_category="missing_capability",
        )
        raise ApiError(409, "agent_capability_missing", "agent does not support services")

    audit = audit_repo.record_intent(
        ControlPlaneAuditEventCreate(
            actor_id=actor.actor_id,
            source_addr=source_addr,
            agent_id=agent_id,
            operation=operation,
            target=target,
            correlation_id=correlation_id,
        )
    )
    commit_audit_intent(audit_repo, audit_health)
    try:
        response = await client.request(agent, method, upstream_path, correlation_id=correlation_id)
    except AgentClientError as exc:
        failure_category = failure_category_for_client_error(method, exc.category)
        audit_repo.finalize(
            audit.id,
            result="failed",
            dispatch_state=exc.dispatch_state,
            failure_category=failure_category,
        )
        commit_audit_outcome(audit_repo, audit_health)
        raise ApiError(
            ERROR_STATUS.get(failure_category, 502), failure_category, "agent request failed"
        ) from exc

    audit_repo.finalize(
        audit.id,
        result="success" if response.status_code < 400 else "failed",
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


@router.get("")
async def list_agent_services(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> Response:
    return await _dispatch(
        agent_id=agent_id,
        upstream_path="/api/services",
        method="GET",
        operation="services.list",
        target="services",
        actor=actor,
        registry=registry,
        availability=availability,
        client=client,
        audit_repo=audit_repo,
        audit_health=audit_health,
        correlation_id=_correlation_id(request),
        source_addr=_source_addr(request),
    )


@router.get("/{service_id}")
async def get_agent_service(
    agent_id: str,
    service_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> Response:
    safe_service_id = service_id.replace("/", "")
    return await _dispatch(
        agent_id=agent_id,
        upstream_path=f"/api/services/{safe_service_id}",
        method="GET",
        operation="services.detail",
        target=f"service:{safe_service_id}",
        actor=actor,
        registry=registry,
        availability=availability,
        client=client,
        audit_repo=audit_repo,
        audit_health=audit_health,
        correlation_id=_correlation_id(request),
        source_addr=_source_addr(request),
    )


@router.get("/{service_id}/events")
async def list_agent_service_events(
    agent_id: str,
    service_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> Response:
    safe_service_id = service_id.replace("/", "")
    return await _dispatch(
        agent_id=agent_id,
        upstream_path=f"/api/services/{safe_service_id}/events",
        method="GET",
        operation="services.events",
        target=f"service:{safe_service_id}",
        actor=actor,
        registry=registry,
        availability=availability,
        client=client,
        audit_repo=audit_repo,
        audit_health=audit_health,
        correlation_id=_correlation_id(request),
        source_addr=_source_addr(request),
    )


@router.get("/{service_id}/logs")
async def get_agent_service_logs(
    agent_id: str,
    service_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> Response:
    safe_service_id = service_id.replace("/", "")
    return await _dispatch(
        agent_id=agent_id,
        upstream_path=f"/api/services/{safe_service_id}/logs",
        method="GET",
        operation="services.logs",
        target=f"service:{safe_service_id}",
        actor=actor,
        registry=registry,
        availability=availability,
        client=client,
        audit_repo=audit_repo,
        audit_health=audit_health,
        correlation_id=_correlation_id(request),
        source_addr=_source_addr(request),
    )


@router.post("/{service_id}/{action}")
async def mutate_agent_service(
    agent_id: str,
    service_id: str,
    action: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> Response:
    safe_service_id = service_id.replace("/", "")
    if action not in {"start", "stop", "restart"}:
        _record_pre_dispatch_failure(
            audit_repo=audit_repo,
            audit_health=audit_health,
            actor=actor,
            agent_id=agent_id,
            operation=f"services.{action}",
            target=f"service:{safe_service_id}",
            correlation_id=_correlation_id(request),
            source_addr=_source_addr(request),
            failure_category="invalid_agent_request",
        )
        raise ApiError(400, "invalid_agent_request", "unsupported service action")
    return await _dispatch(
        agent_id=agent_id,
        upstream_path=f"/api/services/{safe_service_id}/{action}",
        method="POST",
        operation=f"services.{action}",
        target=f"service:{safe_service_id}",
        actor=actor,
        registry=registry,
        availability=availability,
        client=client,
        audit_repo=audit_repo,
        audit_health=audit_health,
        correlation_id=_correlation_id(request),
        source_addr=_source_addr(request),
    )
