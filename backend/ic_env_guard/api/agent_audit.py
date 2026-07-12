from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentHttpClient
from ic_env_guard.api.agent_http import (
    ERROR_STATUS,
    augment_upstream_error_body,
    failure_category_for_client_error,
    get_agent_http_client,
)
from ic_env_guard.api.agent_proxy import get_agent_http_proxy, proxy_get, validate_single_query
from ic_env_guard.api.agents import get_agent_availability
from ic_env_guard.api.audit_health import (
    AuditStorageHealth,
    commit_audit_intent,
    commit_audit_outcome,
    get_audit_storage_health,
)
from ic_env_guard.api.control_plane_audit import get_control_plane_audit_repository
from ic_env_guard.api.errors import ApiError
from ic_env_guard.api.runtime import require_v2_auth
from ic_env_guard.auth.dependencies import AuthContext, require_auth
from ic_env_guard.db.control_plane_audit import (
    ControlPlaneAuditEventCreate,
    ControlPlaneAuditRepository,
)
from ic_env_guard.proxy.http import AgentHttpProxy, AgentProxyError

router = APIRouter(prefix="/api/agents/{agent_id}/audit", tags=["agent-audit"])
v2_router = APIRouter(prefix="/api/v2/agents/{agent_id}/audit", tags=["agent-audit-v2"])


@v2_router.get("")
async def list_agent_audit_events_v2(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    target_type: Annotated[
        str | None, Query(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    ] = None,
    result: Literal["success", "failed", "denied"] | None = None,
):
    validate_single_query(request, {"limit", "target_type", "result"})
    query: dict[str, str | int] = {"limit": limit}
    if target_type is not None:
        query["target_type"] = target_type
    if result is not None:
        query["result"] = result
    return await proxy_get(
        proxy=proxy,
        agent_id=agent_id,
        capability="audit.v1",
        upstream_path="/api/audit",
        query=query,
        operation="audit.list",
        target="audit:events",
        request=request,
        actor=actor,
        audit_repo=audit_repo,
        audit_health=audit_health,
    )


@router.get("")
async def list_agent_audit_events(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    target_type: str | None = None,
    result: str | None = None,
) -> Response:
    correlation_id = getattr(request.state, "correlation_id", None) or str(uuid4())
    source_addr = request.client.host if request.client else None
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
        response = await proxy.with_runtime(client, availability).request_json(
            agent_id=agent_id,
            capability="audit.v1",
            method="GET",
            upstream_path="/api/audit",
            query=params,
            correlation_id=correlation_id,
        )
    except AgentProxyError as exc:
        failure_category = failure_category_for_client_error("GET", exc.code)
        audit_failure_category = (
            "missing_capability"
            if failure_category == "agent_capability_missing"
            else failure_category
        )
        audit_repo.finalize(
            gateway_audit.id,
            result="failed",
            dispatch_state=exc.dispatch_state,
            upstream_status=exc.upstream_status,
            failure_category=audit_failure_category,
        )
        commit_audit_outcome(audit_repo, audit_health)
        raise ApiError(
            ERROR_STATUS.get(failure_category, exc.status_code),
            failure_category,
            "agent request failed",
        ) from exc

    audit_repo.finalize(
        gateway_audit.id,
        result="success" if response.status_code < 400 else "failed",
        dispatch_state="dispatched",
        upstream_status=response.status_code,
    )
    commit_audit_outcome(audit_repo, audit_health)
    body = response.body
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
