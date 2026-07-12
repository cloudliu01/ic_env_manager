from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from ic_env_guard.api.audit_health import (
    AuditStorageHealth,
    commit_audit_intent,
    commit_audit_outcome,
    get_audit_storage_health,
)
from ic_env_guard.api.control_plane_audit import get_control_plane_audit_repository
from ic_env_guard.api.runtime import require_v2_auth
from ic_env_guard.api.v2_errors import V2ApiError
from ic_env_guard.auth.dependencies import AuthContext
from ic_env_guard.db.control_plane_audit import (
    ControlPlaneAuditEventCreate,
    ControlPlaneAuditRepository,
)
from ic_env_guard.discovery.models import DiscoveryJob
from ic_env_guard.discovery.service import DiscoveryService
from ic_env_guard.fleet.models import RegistryConflict, RegistryError

router = APIRouter(prefix="/api/v2/discovery", tags=["discovery-v2"])


class DiscoveryStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_id: str = Field(min_length=1, max_length=64)


def get_discovery_service() -> DiscoveryService:
    raise RuntimeError("DiscoveryService dependency was not configured")


@router.get("/scopes")
def list_scopes(
    _: Annotated[AuthContext, Depends(require_v2_auth)],
    service: Annotated[DiscoveryService, Depends(get_discovery_service)],
):
    scopes = service.scopes()
    return {
        "enabled": bool(scopes),
        "scopes": [
            {
                "id": scope.id,
                "name": scope.name,
                "target_count": service.target_count(scope),
            }
            for scope in scopes
        ],
    }


@router.post("/jobs", status_code=201)
async def start_job(
    body: DiscoveryStartRequest,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    service: Annotated[DiscoveryService, Depends(get_discovery_service)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
):
    audit = _intent(request, actor, audit_repo, audit_health, body.scope_id, "discovery.start")
    try:
        job = service.start(body.scope_id, start_audit_event_id=audit.id)
    except (RegistryConflict, RegistryError) as exc:
        code = str(exc) if isinstance(exc, RegistryConflict) else "discovery_unavailable"
        _failure(audit, audit_repo, audit_health, code)
        status = 404 if code == "discovery_scope_not_found" else 409
        raise V2ApiError(status, code, "discovery job could not be started") from exc
    return {"job": _job(job)}


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    _: Annotated[AuthContext, Depends(require_v2_auth)],
    service: Annotated[DiscoveryService, Depends(get_discovery_service)],
):
    job = service.get(job_id)
    if job is None:
        raise V2ApiError(404, "discovery_job_not_found", "discovery job not found")
    return {"job": _job(job)}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    service: Annotated[DiscoveryService, Depends(get_discovery_service)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
):
    audit = _intent(request, actor, audit_repo, audit_health, job_id, "discovery.cancel")
    try:
        job = service.cancel(job_id)
    except (RegistryConflict, RegistryError) as exc:
        code = str(exc) if isinstance(exc, RegistryConflict) else "discovery_unavailable"
        _failure(audit, audit_repo, audit_health, code)
        raise V2ApiError(409, code, "discovery job could not be cancelled") from exc
    _success(audit, audit_repo, audit_health)
    return {"job": _job(job)}


@router.get("/jobs/{job_id}/results")
def list_results(
    job_id: str,
    _: Annotated[AuthContext, Depends(require_v2_auth)],
    service: Annotated[DiscoveryService, Depends(get_discovery_service)],
):
    try:
        results = service.results(job_id)
    except RegistryConflict as exc:
        raise V2ApiError(404, str(exc), "discovery job not found") from exc
    projected = []
    for item in results:
        status, enrollment_status = service.classify(item)
        projected.append(
            {
                "result_id": item.result_id,
                "candidate_url": item.canonical_url,
                "ip": item.ip,
                "port": item.port,
                "transport_profile_id": item.transport_profile_id,
                "fingerprint_version": item.fingerprint_version,
                "first_seen_at": item.first_seen_at,
                "last_seen_at": item.last_seen_at,
                "status": status,
                "enrollment_status": enrollment_status,
                "linked_enrollment_id": item.linked_enrollment_id,
                "error_code": item.safe_error_code,
            }
        )
    return {"results": projected}


def _job(job: DiscoveryJob):
    return {
        "job_id": job.job_id,
        "scope_id": job.scope_id,
        "state": job.state.value,
        "total_targets": job.total_targets,
        "checked_targets": job.checked_targets,
        "found_targets": job.found_targets,
        "error_code": job.safe_error_code,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _intent(request, actor, repository, health, target, operation):
    try:
        event = repository.record_intent(
            ControlPlaneAuditEventCreate(
                actor_id=actor.actor_id,
                source_addr=request.client.host if request.client else None,
                agent_id=None,
                operation=operation,
                target=f"discovery:{target}",
                correlation_id=getattr(request.state, "correlation_id", None),
            )
        )
        commit_audit_intent(repository, health)
        return event
    except Exception as exc:
        repository.session.rollback()
        health.mark_unhealthy()
        raise V2ApiError(503, "audit_unavailable", "audit storage is unavailable") from exc


def _failure(event, repository, health, code):
    repository.finalize(
        event.id, result="failed", dispatch_state="not_dispatched", failure_category=code
    )
    commit_audit_outcome(repository, health)


def _success(event, repository, health):
    repository.finalize(event.id, result="success", dispatch_state="not_dispatched")
    commit_audit_outcome(repository, health)
