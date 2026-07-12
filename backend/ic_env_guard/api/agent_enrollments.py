from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

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
from ic_env_guard.enrollment.agent_client import EnrollmentValidationError
from ic_env_guard.enrollment.jobs import EnrollmentConflict, EnrollmentJobRequest
from ic_env_guard.enrollment.orchestrator import (
    EnrollmentOrchestrator,
    LegacyValidationRequest,
)
from ic_env_guard.fleet.models import EnrollmentMethod

router = APIRouter(prefix="/api/v2", tags=["agent-enrollments"])


class SshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)


class CreateEnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(min_length=1, max_length=2048)
    display_name: str | None = Field(default=None, max_length=128)
    transport_profile_id: str = Field(min_length=1, max_length=64)
    ssh: SshRequest


class LegacyValidateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(min_length=1, max_length=2048)
    transport_profile_id: str = Field(min_length=1, max_length=64)
    token: SecretStr


def get_enrollment_orchestrator() -> EnrollmentOrchestrator:
    raise RuntimeError("EnrollmentOrchestrator dependency was not configured")


@router.post("/agent-enrollments", status_code=201)
def create_enrollment(
    body: CreateEnrollmentRequest,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    orchestrator: Annotated[EnrollmentOrchestrator, Depends(get_enrollment_orchestrator)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> dict[str, object]:
    audit = _intent(request, actor, audit_repo, audit_health, None, "agent-enrollment.create")
    try:
        result = orchestrator.create(
            EnrollmentJobRequest(
                normalized_endpoint=body.base_url,
                transport_profile_id=body.transport_profile_id,
                display_name=body.display_name,
                ssh_user=body.ssh.user,
                ssh_host=body.ssh.host,
                ssh_port=body.ssh.port,
                enrollment_method=EnrollmentMethod.SSH_CLI,
            )
        )
    except EnrollmentConflict as exc:
        _failure(audit, audit_repo, audit_health, exc.code, "not_dispatched")
        raise _job_error(exc) from exc
    _success(audit, audit_repo, audit_health, "not_dispatched")
    return result.to_public_dict()


@router.get("/agent-enrollments/{enrollment_id}")
def get_enrollment(
    enrollment_id: str,
    _: Annotated[AuthContext, Depends(require_v2_auth)],
    orchestrator: Annotated[EnrollmentOrchestrator, Depends(get_enrollment_orchestrator)],
) -> dict[str, object]:
    try:
        return orchestrator.get(enrollment_id).to_public_dict()
    except EnrollmentConflict as exc:
        raise _job_error(exc) from exc


@router.post("/agent-enrollments/{enrollment_id}/cancel")
def cancel_enrollment(
    enrollment_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    orchestrator: Annotated[EnrollmentOrchestrator, Depends(get_enrollment_orchestrator)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> dict[str, object]:
    audit = _intent(
        request, actor, audit_repo, audit_health, enrollment_id, "agent-enrollment.cancel"
    )
    try:
        result = orchestrator.cancel(enrollment_id).to_public_dict()
    except EnrollmentConflict as exc:
        _failure(audit, audit_repo, audit_health, exc.code, "not_dispatched")
        raise _job_error(exc) from exc
    _success(audit, audit_repo, audit_health, "not_dispatched")
    return result


@router.post("/agents/validate")
async def validate_legacy(
    body: LegacyValidateBody,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    orchestrator: Annotated[EnrollmentOrchestrator, Depends(get_enrollment_orchestrator)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> dict[str, object]:
    audit = _intent(request, actor, audit_repo, audit_health, None, "agent-enrollment.validate")
    try:
        result = await orchestrator.validate_legacy(
            LegacyValidationRequest(body.base_url, body.transport_profile_id),
            body.token.get_secret_value(),
        )
    except EnrollmentConflict as exc:
        _failure(audit, audit_repo, audit_health, exc.code, "not_dispatched")
        raise _job_error(exc) from exc
    except EnrollmentValidationError as exc:
        _failure(audit, audit_repo, audit_health, exc.code, exc.dispatch_state)
        raise V2ApiError(422, exc.code, "agent validation failed") from exc
    _success(audit, audit_repo, audit_health, "dispatched")
    return result.to_public_dict()


def _job_error(exc: EnrollmentConflict) -> V2ApiError:
    status = 404 if exc.code == "agent_enrollment_not_found" else 409
    return V2ApiError(status, exc.code, "agent enrollment request failed")


def _intent(request, actor, repository, health, enrollment_id, operation):
    try:
        event = repository.record_intent(
            ControlPlaneAuditEventCreate(
                actor_id=actor.actor_id,
                source_addr=request.client.host if request.client else None,
                agent_id=None,
                operation=operation,
                target=(
                    f"enrollment:{enrollment_id}" if enrollment_id else "enrollment:new"
                ),
                correlation_id=getattr(request.state, "correlation_id", None),
            )
        )
        commit_audit_intent(repository, health)
        return event
    except Exception as exc:
        session = getattr(repository, "session", None)
        if session is not None:
            session.rollback()
        health.mark_unhealthy()
        raise V2ApiError(503, "audit_unavailable", "audit storage is unavailable") from exc


def _failure(event, repository, health, code, dispatch_state):
    repository.finalize(
        event.id,
        result="failed",
        dispatch_state=dispatch_state,
        failure_category=code,
    )
    commit_audit_outcome(repository, health)


def _success(event, repository, health, dispatch_state):
    repository.finalize(event.id, result="success", dispatch_state=dispatch_state)
    commit_audit_outcome(repository, health)
