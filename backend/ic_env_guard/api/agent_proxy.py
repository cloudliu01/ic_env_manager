from fastapi import Request
from fastapi.responses import JSONResponse

from ic_env_guard.api.audit_health import (
    AuditStorageHealth,
    commit_audit_intent,
    commit_audit_outcome,
)
from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.auth.dependencies import AuthContext
from ic_env_guard.db.control_plane_audit import (
    ControlPlaneAuditEventCreate,
    ControlPlaneAuditRepository,
)
from ic_env_guard.proxy.http import AgentHttpProxy, AgentProxyError


def get_agent_http_proxy() -> AgentHttpProxy:
    raise RuntimeError("AgentHttpProxy dependency was not configured")


async def proxy_get(
    *,
    proxy: AgentHttpProxy,
    agent_id: str,
    capability: str,
    upstream_path: str,
    query: dict[str, str | int],
    operation: str,
    target: str,
    request: Request,
    actor: AuthContext,
    audit_repo: ControlPlaneAuditRepository,
    audit_health: AuditStorageHealth,
    tail: bool = False,
) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", None)
    audit = audit_repo.record_intent(
        ControlPlaneAuditEventCreate(
            actor_id=actor.actor_id,
            source_addr=request.client.host if request.client else None,
            agent_id=agent_id,
            operation=operation,
            target=target,
            correlation_id=correlation_id,
        )
    )
    commit_audit_intent(audit_repo, audit_health)
    try:
        response = await proxy.get_json(
            agent_id=agent_id,
            capability=capability,
            upstream_path=upstream_path,
            query=query,
            correlation_id=correlation_id,
            tail=tail,
        )
    except AgentProxyError as exc:
        audit_repo.finalize(
            audit.id,
            result="failed",
            dispatch_state=exc.dispatch_state,
            upstream_status=exc.upstream_status,
            failure_category=exc.code,
        )
        commit_audit_outcome(audit_repo, audit_health)
        raise V2ApiError(exc.status_code, exc.code, "Agent request failed") from exc
    audit_repo.finalize(
        audit.id,
        result="success" if response.status_code < 400 else "failed",
        dispatch_state=response.dispatch_state,
        upstream_status=response.status_code,
    )
    commit_audit_outcome(audit_repo, audit_health)
    return JSONResponse(response.body, status_code=response.status_code)


def validate_single_query(request: Request, allowed: set[str]) -> None:
    if any(
        key not in allowed or len(request.query_params.getlist(key)) != 1
        for key in request.query_params
    ):
        raise V2ApiError(422, "validation_error", "request validation failed")
