import json as json_module
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.agents.registry import AgentNotFoundError, AgentRegistry
from ic_env_guard.agents.terminal_proxy import GatewayTicketStore
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

router = APIRouter(prefix="/api/agents/{agent_id}/terminals", tags=["agent-terminals"])


def get_gateway_ticket_store() -> GatewayTicketStore:
    raise RuntimeError("GatewayTicketStore dependency was not configured")


def _record_pre_dispatch_failure(
    *,
    audit_repo: ControlPlaneAuditRepository,
    audit_health: AuditStorageHealth,
    actor: AuthContext,
    agent_id: str,
    operation: str,
    target: str,
    failure_category: str,
    correlation_id: str | None = None,
    source_addr: str | None = None,
) -> None:
    correlation_id = correlation_id or str(uuid4())
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
    json: object | None = None,
    params: Mapping[str, str | int] | None = None,
    correlation_id: str | None = None,
    source_addr: str | None = None,
    validate_response: Callable[[httpx.Response], str | None] | None = None,
) -> Response:
    correlation_id = correlation_id or str(uuid4())
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
    if not await availability.ensure_capability(agent_id, "terminals.v1"):
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
        raise ApiError(409, "agent_capability_missing", "agent does not support terminals")

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
        response = await client.request(
            agent,
            method,
            upstream_path,
            correlation_id=correlation_id,
            params=params,
            json=json,
        )
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

    failure_category = validate_response(response) if validate_response is not None else None
    if failure_category is not None:
        audit_repo.finalize(
            audit.id,
            result="failed",
            dispatch_state="dispatched",
            upstream_status=response.status_code,
            failure_category=failure_category,
        )
        commit_audit_outcome(audit_repo, audit_health)
        raise ApiError(
            ERROR_STATUS.get(failure_category, 502), failure_category, "agent request failed"
        )

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


def _validate_connect_token_response(response: httpx.Response) -> str | None:
    if response.status_code >= 400:
        return None
    try:
        payload = response.json()
        ticket = payload["ticket"]
        expires_in_seconds = int(payload["expires_in_seconds"])
    except (KeyError, TypeError, ValueError):
        return "agent_protocol_error"
    if not isinstance(ticket, str) or ticket == "" or expires_in_seconds <= 0:
        return "agent_protocol_error"
    return None


@router.get("")
async def list_agent_terminals(
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
        upstream_path="/api/terminals",
        method="GET",
        operation="terminals.list",
        target="terminals",
        actor=actor,
        registry=registry,
        availability=availability,
        client=client,
        audit_repo=audit_repo,
        audit_health=audit_health,
        correlation_id=getattr(request.state, "correlation_id", None),
        source_addr=request.client.host if request.client else None,
    )


@router.post("")
async def create_agent_terminal(
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
        upstream_path="/api/terminals",
        method="POST",
        operation="terminals.create",
        target="terminals",
        actor=actor,
        registry=registry,
        availability=availability,
        client=client,
        audit_repo=audit_repo,
        audit_health=audit_health,
        json=await request.json(),
        correlation_id=getattr(request.state, "correlation_id", None),
        source_addr=request.client.host if request.client else None,
    )


@router.get("/{terminal_id}")
async def get_agent_terminal(
    agent_id: str,
    terminal_id: str,
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
        upstream_path=f"/api/terminals/{terminal_id}",
        method="GET",
        operation="terminals.detail",
        target=f"terminal:{terminal_id}",
        actor=actor,
        registry=registry,
        availability=availability,
        client=client,
        audit_repo=audit_repo,
        audit_health=audit_health,
        correlation_id=getattr(request.state, "correlation_id", None),
        source_addr=request.client.host if request.client else None,
    )


@router.get("/{terminal_id}/history")
async def get_agent_terminal_history(
    agent_id: str,
    terminal_id: str,
    request: Request,
    cursor: int,
    actor: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> Response:
    return await _dispatch(
        agent_id=agent_id,
        upstream_path=f"/api/terminals/{terminal_id}/history",
        method="GET",
        operation="terminals.history",
        target=f"terminal:{terminal_id}",
        actor=actor,
        registry=registry,
        availability=availability,
        client=client,
        audit_repo=audit_repo,
        audit_health=audit_health,
        params={"cursor": cursor},
        correlation_id=getattr(request.state, "correlation_id", None),
        source_addr=request.client.host if request.client else None,
    )


@router.post("/{terminal_id}/resize")
async def resize_agent_terminal(
    agent_id: str,
    terminal_id: str,
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
        upstream_path=f"/api/terminals/{terminal_id}/resize",
        method="POST",
        operation="terminals.resize",
        target=f"terminal:{terminal_id}",
        actor=actor,
        registry=registry,
        availability=availability,
        client=client,
        audit_repo=audit_repo,
        audit_health=audit_health,
        json=await request.json(),
        correlation_id=getattr(request.state, "correlation_id", None),
        source_addr=request.client.host if request.client else None,
    )


@router.post("/{terminal_id}/connect-token")
async def create_agent_terminal_connect_token(
    agent_id: str,
    terminal_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
    tickets: Annotated[GatewayTicketStore, Depends(get_gateway_ticket_store)],
) -> Response:
    reservation = tickets.reserve(agent_id)
    if reservation is None:
        _record_pre_dispatch_failure(
            audit_repo=audit_repo,
            audit_health=audit_health,
            actor=actor,
            agent_id=agent_id,
            operation="terminals.connect-token",
            target=f"terminal:{terminal_id}",
            correlation_id=getattr(request.state, "correlation_id", None),
            source_addr=request.client.host if request.client else None,
            failure_category="gateway_capacity_exceeded",
        )
        raise ApiError(429, "gateway_capacity_exceeded", "gateway ticket capacity exceeded")
    committed = False
    try:
        response = await _dispatch(
            agent_id=agent_id,
            upstream_path=f"/api/terminals/{terminal_id}/connect-token",
            method="POST",
            operation="terminals.connect-token",
            target=f"terminal:{terminal_id}",
            actor=actor,
            registry=registry,
            availability=availability,
            client=client,
            audit_repo=audit_repo,
            audit_health=audit_health,
            correlation_id=getattr(request.state, "correlation_id", None),
            source_addr=request.client.host if request.client else None,
            validate_response=_validate_connect_token_response,
        )
        if not isinstance(response, JSONResponse):
            return response
        if response.status_code >= 400:
            return response
        body = response.body.decode("utf-8")
        payload = json_module.loads(body)
        gateway_ticket = tickets.commit(
            reservation,
            actor_id=actor.actor_id,
            agent_id=agent_id,
            terminal_id=terminal_id,
            intended_ws_path=f"/ws/agents/{agent_id}/terminals/{terminal_id}",
            upstream_ticket=payload["ticket"],
            expires_at=datetime.now(UTC) + timedelta(seconds=int(payload["expires_in_seconds"])),
        )
        committed = True
        return JSONResponse(
            status_code=response.status_code,
            content={
                "ticket": gateway_ticket.ticket,
                "expires_in_seconds": payload["expires_in_seconds"],
            },
        )
    finally:
        if not committed:
            tickets.release_reservation(reservation)


@router.delete("/{terminal_id}")
async def close_agent_terminal(
    agent_id: str,
    terminal_id: str,
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
        upstream_path=f"/api/terminals/{terminal_id}",
        method="DELETE",
        operation="terminals.close",
        target=f"terminal:{terminal_id}",
        actor=actor,
        registry=registry,
        availability=availability,
        client=client,
        audit_repo=audit_repo,
        audit_health=audit_health,
        correlation_id=getattr(request.state, "correlation_id", None),
        source_addr=request.client.host if request.client else None,
    )
