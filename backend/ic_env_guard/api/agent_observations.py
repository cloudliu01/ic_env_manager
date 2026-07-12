import re
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ic_env_guard.api.agent_proxy import (
    get_agent_http_proxy,
    proxy_get,
    validate_single_query,
)
from ic_env_guard.api.audit_health import AuditStorageHealth, get_audit_storage_health
from ic_env_guard.api.control_plane_audit import get_control_plane_audit_repository
from ic_env_guard.api.runtime import require_v2_auth
from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.auth.dependencies import AuthContext
from ic_env_guard.db.control_plane_audit import ControlPlaneAuditRepository
from ic_env_guard.observations.models import ObservationStatus
from ic_env_guard.proxy.http import AgentHttpProxy

router = APIRouter(prefix="/api/v2/agents/{agent_id}/observations", tags=["agent-observations-v2"])
_IDENTITY = re.compile(r"^[0-9a-f]{64}$")


@router.get("")
async def list_agent_observations(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
    namespace: Annotated[
        str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    ] = None,
    name: Annotated[
        str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,126}$")
    ] = None,
    status: ObservationStatus | None = None,
    include_stale: bool = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
):
    validate_single_query(
        request, {"namespace", "name", "status", "include_stale", "limit", "cursor"}
    )
    query: dict[str, str | int] = {
        "include_stale": str(include_stale).lower(),
        "limit": limit,
    }
    for key, value in (("namespace", namespace), ("name", name), ("cursor", cursor)):
        if value is not None:
            query[key] = value
    if status is not None:
        query["status"] = status
    return await proxy_get(
        proxy=proxy,
        agent_id=agent_id,
        capability="observations.v2",
        upstream_path="/api/v2/observations",
        query=query,
        operation="observations.list",
        target="observations",
        request=request,
        actor=actor,
        audit_repo=audit_repo,
        audit_health=audit_health,
    )


@router.get("/{identity_key}")
async def get_agent_observation(
    agent_id: str,
    identity_key: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    proxy: Annotated[AgentHttpProxy, Depends(get_agent_http_proxy)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
    include_stale: bool = False,
):
    validate_single_query(request, {"include_stale"})
    if _IDENTITY.fullmatch(identity_key) is None:
        raise V2ApiError(422, "validation_error", "request validation failed")
    return await proxy_get(
        proxy=proxy,
        agent_id=agent_id,
        capability="observations.v2",
        upstream_path=f"/api/v2/observations/{identity_key}",
        query={"include_stale": str(include_stale).lower()},
        operation="observations.detail",
        target=f"observation:{identity_key}",
        request=request,
        actor=actor,
        audit_repo=audit_repo,
        audit_health=audit_health,
    )
