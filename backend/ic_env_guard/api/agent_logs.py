import re
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ic_env_guard.api.agent_proxy import get_agent_http_proxy, proxy_get, validate_single_query
from ic_env_guard.api.audit_health import AuditStorageHealth, get_audit_storage_health
from ic_env_guard.api.control_plane_audit import get_control_plane_audit_repository
from ic_env_guard.api.runtime import require_v2_auth
from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.auth.dependencies import AuthContext
from ic_env_guard.db.control_plane_audit import ControlPlaneAuditRepository
from ic_env_guard.proxy.http import AgentHttpProxy

router = APIRouter(prefix="/api/v2/agents/{agent_id}/logs", tags=["agent-logs-v2"])
_LOG_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,126}$")


def _valid_log_id(value: str) -> str:
    if _LOG_ID.fullmatch(value) is None:
        raise V2ApiError(422, "validation_error", "request validation failed")
    return value


async def _dispatch(
    agent_id,
    request,
    actor,
    proxy,
    audit_repo,
    audit_health,
    *,
    path,
    operation,
    target,
    query=None,
    tail=False,
):
    return await proxy_get(
        proxy=proxy,
        agent_id=agent_id,
        capability="logs.v2",
        upstream_path=path,
        query=query or {},
        operation=operation,
        target=target,
        request=request,
        actor=actor,
        audit_repo=audit_repo,
        audit_health=audit_health,
        tail=tail,
    )


@router.get("")
async def list_agent_logs(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
):
    validate_single_query(request, set())
    return await _dispatch(
        agent_id,
        request,
        actor,
        proxy,
        audit_repo,
        audit_health,
        path="/api/v2/logs",
        operation="logs.list",
        target="logs",
    )


@router.get("/{log_id}")
async def get_agent_log(
    agent_id: str,
    log_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
):
    validate_single_query(request, set())
    log_id = _valid_log_id(log_id)
    return await _dispatch(
        agent_id,
        request,
        actor,
        proxy,
        audit_repo,
        audit_health,
        path=f"/api/v2/logs/{log_id}",
        operation="logs.detail",
        target=f"log:{log_id}",
    )


@router.get("/{log_id}/tail")
async def tail_agent_log(
    agent_id: str,
    log_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
    lines: Annotated[int, Query(ge=1, le=1000)] = 100,
):
    validate_single_query(request, {"lines"})
    log_id = _valid_log_id(log_id)
    return await _dispatch(
        agent_id,
        request,
        actor,
        proxy,
        audit_repo,
        audit_health,
        path=f"/api/v2/logs/{log_id}/tail",
        operation="logs.tail",
        target=f"log:{log_id}",
        query={"lines": lines},
        tail=True,
    )
