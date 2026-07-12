import re
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
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
from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.auth.dependencies import AuthContext, require_auth
from ic_env_guard.db.control_plane_audit import (
    ControlPlaneAuditEventCreate,
    ControlPlaneAuditRepository,
)
from ic_env_guard.proxy.http import AgentHttpProxy, AgentProxyError

router = APIRouter(prefix="/api/agents/{agent_id}/services", tags=["agent-services"])
v2_router = APIRouter(prefix="/api/v2/agents/{agent_id}/services", tags=["agent-services-v2"])
_SERVICE_ID = re.compile(r"^[a-zA-Z0-9_.-]{1,127}$")


def _service_id(value: str) -> str:
    if value in {".", ".."} or _SERVICE_ID.fullmatch(value) is None:
        raise V2ApiError(422, "validation_error", "request validation failed")
    return value


async def _v2_services_get(
    agent_id, request, actor, proxy, audit_repo, audit_health, *, path, operation, target
):
    validate_single_query(request, set())
    return await proxy_get(
        proxy=proxy,
        agent_id=agent_id,
        capability="services.v1",
        upstream_path=path,
        query={},
        operation=operation,
        target=target,
        request=request,
        actor=actor,
        audit_repo=audit_repo,
        audit_health=audit_health,
    )


@v2_router.get("")
async def v2_list_agent_services(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
):
    return await _v2_services_get(
        agent_id,
        request,
        actor,
        proxy,
        audit_repo,
        audit_health,
        path="/api/services",
        operation="services.list",
        target="services",
    )


@v2_router.get("/{service_id}")
async def v2_get_agent_service(
    agent_id: str,
    service_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
):
    service_id = _service_id(service_id)
    return await _v2_services_get(
        agent_id,
        request,
        actor,
        proxy,
        audit_repo,
        audit_health,
        path=f"/api/services/{service_id}",
        operation="services.detail",
        target=f"service:{service_id}",
    )


@v2_router.get("/{service_id}/events")
async def v2_list_agent_service_events(
    agent_id: str,
    service_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
):
    service_id = _service_id(service_id)
    return await _v2_services_get(
        agent_id,
        request,
        actor,
        proxy,
        audit_repo,
        audit_health,
        path=f"/api/services/{service_id}/events",
        operation="services.events",
        target=f"service:{service_id}",
    )


@v2_router.get("/{service_id}/logs")
async def v2_get_agent_service_logs(
    agent_id: str,
    service_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
):
    service_id = _service_id(service_id)
    return await _v2_services_get(
        agent_id,
        request,
        actor,
        proxy,
        audit_repo,
        audit_health,
        path=f"/api/services/{service_id}/logs",
        operation="services.logs",
        target=f"service:{service_id}",
    )


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
    availability: AgentAvailabilityService,
    client: AgentHttpClient,
    proxy: AgentHttpProxy,
    audit_repo: ControlPlaneAuditRepository,
    audit_health: AuditStorageHealth,
    correlation_id: str,
    source_addr: str | None,
) -> Response:
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
        response = await proxy.with_runtime(client, availability).request_json(
            agent_id=agent_id,
            capability="services.v1",
            method=method,
            upstream_path=upstream_path,
            query={},
            correlation_id=correlation_id,
        )
    except AgentProxyError as exc:
        failure_category = failure_category_for_client_error(method, exc.code)
        audit_failure_category = (
            "missing_capability"
            if failure_category == "agent_capability_missing"
            else failure_category
        )
        audit_repo.finalize(
            audit.id,
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
            response.body,
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
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
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
        availability=availability,
        client=client,
        proxy=proxy,
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
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
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
        availability=availability,
        client=client,
        proxy=proxy,
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
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
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
        availability=availability,
        client=client,
        proxy=proxy,
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
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
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
        availability=availability,
        client=client,
        proxy=proxy,
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
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
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
        availability=availability,
        client=client,
        proxy=proxy,
        audit_repo=audit_repo,
        audit_health=audit_health,
        correlation_id=_correlation_id(request),
        source_addr=_source_addr(request),
    )
