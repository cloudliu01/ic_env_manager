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
from ic_env_guard.api.agent_proxy import get_agent_http_proxy
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
from ic_env_guard.fleet.models import EnrollmentMethod
from ic_env_guard.proxy.http import AgentHttpProxy, AgentProxyError

router = APIRouter(prefix="/api/agents/{agent_id}/monitoring", tags=["agent-monitoring"])


@router.get("/snapshot")
async def get_agent_monitoring_snapshot(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    availability: Annotated[AgentAvailabilityService, Depends(get_agent_availability)],
    client: Annotated[AgentHttpClient, Depends(get_agent_http_client)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
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
    if not await availability.ensure_capability(agent_id, "monitoring.snapshot.v1"):
        _record_failure(
            audit_repo,
            actor,
            agent_id,
            correlation_id,
            "missing_capability",
            audit_health,
            source_addr,
        )
        raise ApiError(
            409,
            "agent_capability_missing",
            "agent does not support monitoring snapshots",
        )

    audit = audit_repo.record_intent(
        ControlPlaneAuditEventCreate(
            actor_id=actor.actor_id,
            source_addr=source_addr,
            agent_id=agent_id,
            operation="monitoring.snapshot",
            target="monitoring:snapshot",
            correlation_id=correlation_id,
        )
    )
    commit_audit_intent(audit_repo, audit_health)
    record = registry.record(agent_id)
    legacy_config_projection = record is not None and (
        record.source == "config_import"
        and record.enrollment_method == EnrollmentMethod.LEGACY_ADMIN_TOKEN
        and record.transport_profile_id == "legacy-config-http"
    )
    try:
        if agent.managed_credential and not legacy_config_projection:
            managed_response = await proxy.request_json(
                agent_id=agent_id,
                capability=None,
                method="GET",
                upstream_path="/api/monitoring/local",
                query={},
                correlation_id=correlation_id,
            )
            response_status = managed_response.status_code
            response_body = managed_response.body
        else:
            legacy_response = await client.request(
                agent,
                "GET",
                "/api/monitoring/local",
                correlation_id=correlation_id,
            )
            response_status = legacy_response.status_code
            response_body = legacy_response.json()
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
    except AgentProxyError as exc:
        failure_category = failure_category_for_client_error("GET", exc.code)
        audit_repo.finalize(
            audit.id,
            result="failed",
            dispatch_state=exc.dispatch_state,
            upstream_status=exc.upstream_status,
            failure_category=failure_category,
        )
        commit_audit_outcome(audit_repo, audit_health)
        raise ApiError(
            (
                502
                if failure_category == "agent_auth_error"
                else ERROR_STATUS.get(failure_category, exc.status_code)
            ),
            failure_category,
            "agent request failed",
        ) from exc

    audit_repo.finalize(
        audit.id,
        result="success" if response_status < 400 else "failed",
        dispatch_state="dispatched",
        upstream_status=response_status,
    )
    commit_audit_outcome(audit_repo, audit_health)
    return JSONResponse(
        status_code=response_status,
        content=augment_upstream_error_body(
            response_body,
            agent_id=agent_id,
            correlation_id=correlation_id,
            status_code=response_status,
        ),
    )


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
            operation="monitoring.snapshot",
            target="monitoring:snapshot",
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
