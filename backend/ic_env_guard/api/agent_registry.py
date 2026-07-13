from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from ic_env_guard.agents.registry import (
    AgentInvalidConfigurationError,
    AgentNotFoundError,
    AgentRegistry,
)
from ic_env_guard.api.agents import get_agent_registry
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
from ic_env_guard.enrollment.jobs import EnrollmentConflict, job_input_fingerprint
from ic_env_guard.enrollment.orchestrator import (
    AutoEnrollmentAuditContext,
    EnrollmentOrchestrator,
    MutationSagaError,
)
from ic_env_guard.enrollment.ssh_config import SshConfigError, validate_ssh_destination
from ic_env_guard.fleet.models import RegistryConflict, RegistryError
from ic_env_guard.fleet.probes import AgentProbeDisabled, AgentProbeError, FleetProbeService
from ic_env_guard.fleet.status import FleetStatusService, InvalidFleetCursor

router = APIRouter(prefix="/api/v2/agents", tags=["agent-registry-v2"])


class AgentEnabledRequest(BaseModel):
    enabled: bool


class AddAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enrollment_id: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=128)


class RotationSshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)


class RotationStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["start"]
    ssh: RotationSshRequest


class RotationConsumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["consume"]
    enrollment_id: str = Field(min_length=1, max_length=128)


RotationRequest = RotationStartRequest | RotationConsumeRequest
_ROTATION_REQUEST = TypeAdapter(RotationRequest)


class UpdateAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    transport_profile_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def require_change(self) -> "UpdateAgentRequest":
        if all(
            value is None
            for value in (
                self.display_name,
                self.enabled,
                self.base_url,
                self.transport_profile_id,
            )
        ):
            raise ValueError("at least one Agent field is required")
        return self


class RemoveAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_remote_residual: bool = False


def get_fleet_status_service() -> FleetStatusService:
    raise RuntimeError("FleetStatusService dependency was not configured")


def get_fleet_probe_service() -> FleetProbeService:
    raise RuntimeError("FleetProbeService dependency was not configured")


def get_enrollment_orchestrator() -> EnrollmentOrchestrator:
    raise RuntimeError("EnrollmentOrchestrator dependency was not configured")


@router.post("", status_code=201)
async def add_agent(
    body: AddAgentRequest,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    orchestrator: Annotated[EnrollmentOrchestrator, Depends(get_enrollment_orchestrator)],
    service: Annotated[FleetStatusService, Depends(get_fleet_status_service)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> dict[str, object]:
    audit = _intent(
        request, actor, audit_repo, audit_health, body.enrollment_id, "agents.v2.create"
    )
    try:
        current = orchestrator.get(body.enrollment_id).job
        record = await orchestrator.consume(
            body.enrollment_id,
            display_name=body.display_name,
            input_fingerprint=job_input_fingerprint(current),
        )
    except MutationSagaError as exc:
        _failure(
            audit,
            audit_repo,
            audit_health,
            exc.code,
            dispatch_state=exc.dispatch_state,
        )
        status = (
            503
            if exc.code
            in {
                "agent_network_error",
                "agent_timeout",
                "agent_registry_unavailable",
                "agent_enrollment_activation_pending",
            }
            else 409
        )
        raise V2ApiError(status, exc.code, "agent enrollment request failed") from exc
    except EnrollmentConflict as exc:
        _failure(
            audit,
            audit_repo,
            audit_health,
            exc.code,
            dispatch_state="not_dispatched",
        )
        if exc.code == "agent_enrollment_not_found":
            status = 404
        elif exc.code in {
            "agent_network_error",
            "agent_timeout",
            "agent_registry_unavailable",
            "agent_enrollment_activation_pending",
        }:
            status = 503
        else:
            status = 409
        raise V2ApiError(status, exc.code, "agent enrollment request failed") from exc
    except RegistryError as exc:
        _failure(
            audit,
            audit_repo,
            audit_health,
            "agent_registry_unavailable",
            dispatch_state="not_dispatched",
        )
        raise V2ApiError(
            503, "agent_registry_unavailable", "agent enrollment request failed"
        ) from exc
    _success(
        audit,
        audit_repo,
        audit_health,
        dispatch_state=(
            "not_dispatched"
            if record.enrollment_method.value == "legacy_admin_token"
            else "dispatched"
        ),
    )
    projected = service.get(record.agent_id)
    assert projected is not None
    return {"agent": projected}


@router.post("/{agent_id}/credential-rotation")
async def rotate_agent_credential(
    agent_id: str,
    body: RotationRequest,
    response: Response,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    orchestrator: Annotated[EnrollmentOrchestrator, Depends(get_enrollment_orchestrator)],
    service: Annotated[FleetStatusService, Depends(get_fleet_status_service)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> dict[str, object]:
    parsed = _ROTATION_REQUEST.validate_python(body)
    audit = _intent(
        request, actor, audit_repo, audit_health, agent_id, "agents.v2.credential-rotation"
    )
    try:
        if isinstance(parsed, RotationStartRequest):
            try:
                validate_ssh_destination(
                    user=parsed.ssh.user,
                    host=parsed.ssh.host,
                    port=parsed.ssh.port,
                )
            except SshConfigError as exc:
                raise EnrollmentConflict("ssh_destination_invalid") from exc
            result = orchestrator.start_rotation(
                agent_id,
                ssh_user=parsed.ssh.user,
                ssh_host=parsed.ssh.host,
                ssh_port=parsed.ssh.port,
                audit_context=AutoEnrollmentAuditContext(
                    actor.actor_id,
                    request.client.host if request.client else None,
                    getattr(request.state, "correlation_id", None),
                ),
            )
            response.status_code = 201
            _success(audit, audit_repo, audit_health, dispatch_state="not_dispatched")
            return {"rotation": result.to_public_dict()}
        record = await orchestrator.consume_rotation(agent_id, parsed.enrollment_id)
    except MutationSagaError as exc:
        _failure(
            audit,
            audit_repo,
            audit_health,
            exc.code,
            dispatch_state=exc.dispatch_state,
        )
        status = (
            503
            if exc.code
            in {
                "agent_network_error",
                "agent_timeout",
                "agent_registry_unavailable",
                "agent_enrollment_activation_pending",
            }
            else 409
        )
        raise V2ApiError(status, exc.code, "credential rotation failed") from exc
    except EnrollmentConflict as exc:
        _failure(
            audit,
            audit_repo,
            audit_health,
            exc.code,
            dispatch_state="not_dispatched",
        )
        if exc.code in {"agent_not_found", "agent_enrollment_not_found"}:
            status = 404
        elif exc.code == "ssh_destination_invalid":
            status = 422
        elif exc.code in {
            "agent_network_error",
            "agent_timeout",
            "agent_registry_unavailable",
            "agent_enrollment_activation_pending",
        }:
            status = 503
        else:
            status = 409
        raise V2ApiError(status, exc.code, "credential rotation failed") from exc
    except RegistryError as exc:
        _failure(
            audit,
            audit_repo,
            audit_health,
            "agent_registry_unavailable",
            dispatch_state="not_dispatched",
        )
        raise V2ApiError(
            503, "agent_registry_unavailable", "credential rotation failed"
        ) from exc
    _success(audit, audit_repo, audit_health, dispatch_state="dispatched")
    projected = service.get(record.agent_id)
    assert projected is not None
    return {
        "agent": projected,
        "rotation": {
            "enrollment_id": parsed.enrollment_id,
            "state": "consumed",
            "residual_warning": None,
        },
    }


@router.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    orchestrator: Annotated[EnrollmentOrchestrator, Depends(get_enrollment_orchestrator)],
    service: Annotated[FleetStatusService, Depends(get_fleet_status_service)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> dict[str, object]:
    audit = _intent(request, actor, audit_repo, audit_health, agent_id, "agents.v2.update")
    try:
        before = orchestrator.registry.get(agent_id)
        dispatched = before is not None and (
            (body.base_url is not None and body.base_url != before.normalized_endpoint)
            or (
                body.transport_profile_id is not None
                and body.transport_profile_id != before.transport_profile_id
            )
        )
        record = await orchestrator.update_agent(
            agent_id,
            display_name=body.display_name,
            enabled=body.enabled,
            base_url=body.base_url,
            transport_profile_id=body.transport_profile_id,
        )
    except MutationSagaError as exc:
        _failure(
            audit,
            audit_repo,
            audit_health,
            exc.code,
            dispatch_state=exc.dispatch_state,
        )
        if exc.code == "agent_not_found":
            status = 404
        elif exc.code in {
            "agent_network_error",
            "agent_timeout",
            "agent_validation_unavailable",
            "agent_registry_unavailable",
        }:
            status = 503
        elif exc.code in {
            "agent_url_invalid",
            "target_address_forbidden",
            "transport_profile_unknown",
        }:
            status = 422
        else:
            status = 409
        raise V2ApiError(status, exc.code, "agent update failed") from exc
    except EnrollmentConflict as exc:
        _failure(
            audit,
            audit_repo,
            audit_health,
            exc.code,
            dispatch_state="not_dispatched",
        )
        if exc.code == "agent_not_found":
            status = 404
        elif exc.code in {
            "agent_network_error",
            "agent_timeout",
            "agent_validation_unavailable",
            "agent_registry_unavailable",
        }:
            status = 503
        elif exc.code in {
            "agent_url_invalid",
            "target_address_forbidden",
            "transport_profile_unknown",
        }:
            status = 422
        else:
            status = 409
        raise V2ApiError(status, exc.code, "agent update failed") from exc
    except RegistryError as exc:
        _failure(
            audit,
            audit_repo,
            audit_health,
            "agent_registry_unavailable",
            dispatch_state="not_dispatched",
        )
        raise V2ApiError(503, "agent_registry_unavailable", "agent update failed") from exc
    _success(
        audit,
        audit_repo,
        audit_health,
        dispatch_state="dispatched" if dispatched else "not_dispatched",
    )
    projected = service.get(record.agent_id)
    assert projected is not None
    return {"agent": projected}


@router.delete("/{agent_id}", status_code=204)
async def remove_agent(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    orchestrator: Annotated[EnrollmentOrchestrator, Depends(get_enrollment_orchestrator)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
    local_only: bool = Query(default=False),
    body: RemoveAgentRequest | None = None,
) -> Response:
    audit = _intent(request, actor, audit_repo, audit_health, agent_id, "agents.v2.remove")
    if local_only and (body is None or not body.confirm_remote_residual):
        _failure(
            audit,
            audit_repo,
            audit_health,
            "local_only_confirmation_required",
            dispatch_state="not_dispatched",
        )
        raise V2ApiError(
            422,
            "local_only_confirmation_required",
            "local-only removal requires explicit remote residual confirmation",
        )
    try:
        captured = orchestrator.registry.get(agent_id)
    except RegistryError as exc:
        _failure(
            audit,
            audit_repo,
            audit_health,
            "agent_registry_unavailable",
            dispatch_state="not_dispatched",
        )
        raise V2ApiError(
            503, "agent_registry_unavailable", "agent removal failed"
        ) from exc
    remote_dispatch_expected = (
        not local_only
        and captured is not None
        and captured.enrollment_method.value != "legacy_admin_token"
    )
    try:
        await orchestrator.remove_agent(
            agent_id,
            audit_event_id=audit.id,
            local_only=local_only,
        )
    except MutationSagaError as exc:
        if not exc.recoverable:
            _failure(
                audit,
                audit_repo,
                audit_health,
                exc.code,
                dispatch_state=exc.dispatch_state,
            )
        status = (
            409
            if exc.code
            in {"agent_in_use", "agent_changed", "agent_removal_in_progress"}
            else 503
        )
        raise V2ApiError(status, exc.code, "agent removal failed") from exc
    except EnrollmentConflict as exc:
        try:
            recoverable = orchestrator.removal_is_recoverable(audit.id)
        except RegistryError:
            recoverable = False
        if not recoverable:
            _failure(
                audit,
                audit_repo,
                audit_health,
                exc.code,
                dispatch_state="not_dispatched",
            )
        if exc.code == "agent_not_found":
            status = 404
        elif exc.code in {"agent_in_use", "agent_changed", "agent_removal_in_progress"}:
            status = 409
        else:
            status = 503
        raise V2ApiError(status, exc.code, "agent removal failed") from exc
    if local_only:
        audit_repo.finalize(
            audit.id,
            result="success",
            dispatch_state="not_dispatched",
            failure_category="remote_credential_residual",
        )
        commit_audit_outcome(audit_repo, audit_health)
    else:
        _success(
            audit,
            audit_repo,
            audit_health,
            dispatch_state="dispatched" if remote_dispatch_expected else "not_dispatched",
        )
    return Response(status_code=204)


@router.get("")
def list_agents(
    _: Annotated[AuthContext, Depends(require_v2_auth)],
    service: Annotated[FleetStatusService, Depends(get_fleet_status_service)],
    query: str | None = Query(default=None, max_length=256),
    connection_status: Literal[
        "disabled", "unknown", "ready", "degraded", "unavailable"
    ]
    | None = None,
    workload_status: Literal["unknown", "healthy", "warning", "critical", "stale"]
    | None = None,
    capability: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=100, ge=1, le=1000),
    cursor: str | None = Query(default=None, max_length=512),
) -> dict[str, object]:
    try:
        page = service.list(
            query=query,
            connection_status=connection_status,
            workload_status=workload_status,
            capability=capability,
            limit=limit,
            cursor=cursor,
        )
    except InvalidFleetCursor as exc:
        raise V2ApiError(422, "invalid_cursor", "cursor is invalid") from exc
    return {"agents": list(page.agents), "next_cursor": page.next_cursor}


@router.get("/{agent_id}")
def get_agent(
    agent_id: str,
    _: Annotated[AuthContext, Depends(require_v2_auth)],
    service: Annotated[FleetStatusService, Depends(get_fleet_status_service)],
) -> dict[str, object]:
    agent = service.get(agent_id)
    if agent is None:
        raise V2ApiError(404, "agent_not_found", "agent not found")
    return {"agent": agent}


@router.post("/{agent_id}/enabled")
def set_enabled(
    agent_id: str,
    body: AgentEnabledRequest,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    registry: Annotated[AgentRegistry, Depends(get_agent_registry)],
    service: Annotated[FleetStatusService, Depends(get_fleet_status_service)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> dict[str, object]:
    audit = _intent(
        request, actor, audit_repo, audit_health, agent_id, "agents.v2.enabled"
    )
    try:
        registry.set_enabled(agent_id, body.enabled)
    except AgentNotFoundError as exc:
        _failure(
            audit, audit_repo, audit_health, "agent_not_found", dispatch_state="not_dispatched"
        )
        raise V2ApiError(404, "agent_not_found", "agent not found") from exc
    except AgentInvalidConfigurationError as exc:
        _failure(
            audit,
            audit_repo,
            audit_health,
            "agent_invalid_configuration",
            dispatch_state="not_dispatched",
        )
        raise V2ApiError(
            409,
            "agent_invalid_configuration",
            "agent cannot be enabled with its current configuration",
        ) from exc
    except RegistryConflict as exc:
        code = (
            "agent_mutation_in_progress"
            if str(exc) == "agent_mutation_in_progress"
            else "agent_registry_conflict"
        )
        _failure(
            audit,
            audit_repo,
            audit_health,
            code,
            dispatch_state="not_dispatched",
        )
        raise V2ApiError(409, code, "agent mutation is in progress") from exc
    _success(audit, audit_repo, audit_health, dispatch_state="not_dispatched")
    agent = service.get(agent_id)
    assert agent is not None
    return {"agent": agent}


@router.post("/{agent_id}/probe")
async def probe(
    agent_id: str,
    request: Request,
    actor: Annotated[AuthContext, Depends(require_v2_auth)],
    probe_service: Annotated[FleetProbeService, Depends(get_fleet_probe_service)],
    status_service: Annotated[FleetStatusService, Depends(get_fleet_status_service)],
    audit_repo: Annotated[ControlPlaneAuditRepository, Depends(get_control_plane_audit_repository)],
    audit_health: Annotated[AuditStorageHealth, Depends(get_audit_storage_health)],
) -> dict[str, object]:
    audit = _intent(request, actor, audit_repo, audit_health, agent_id, "agents.v2.probe")
    current = status_service.get(agent_id)
    if current is None:
        _failure(
            audit, audit_repo, audit_health, "agent_not_found", dispatch_state="not_dispatched"
        )
        raise V2ApiError(404, "agent_not_found", "agent not found")
    if not current["enabled"]:
        _failure(
            audit, audit_repo, audit_health, "agent_disabled", dispatch_state="not_dispatched"
        )
        raise V2ApiError(409, "agent_disabled", "agent is disabled")
    try:
        result = await probe_service.probe(agent_id)
    except AgentProbeDisabled as exc:
        _failure(
            audit, audit_repo, audit_health, exc.code, dispatch_state=exc.dispatch_state
        )
        raise V2ApiError(409, exc.code, "agent is disabled") from exc
    except AgentProbeError as exc:
        _failure(
            audit, audit_repo, audit_health, exc.code, dispatch_state=exc.dispatch_state
        )
        status_code = 404 if exc.code == "agent_not_found" else 409
        raise V2ApiError(status_code, exc.code, "agent probe failed") from exc
    if result.status.connection_status == "unavailable":
        _failure(
            audit,
            audit_repo,
            audit_health,
            result.status.last_error_code or "agent_unavailable",
            dispatch_state=result.dispatch_state,
        )
    else:
        _success(audit, audit_repo, audit_health, dispatch_state=result.dispatch_state)
    agent = status_service.get(agent_id)
    assert agent is not None
    return {"agent": agent}


def _intent(
    request: Request,
    actor: AuthContext,
    repository: ControlPlaneAuditRepository,
    health: AuditStorageHealth,
    agent_id: str,
    operation: str,
):
    try:
        event = repository.record_intent(
            ControlPlaneAuditEventCreate(
                actor_id=actor.actor_id,
                source_addr=request.client.host if request.client else None,
                agent_id=agent_id,
                operation=operation,
                target=f"agent:{agent_id}",
                correlation_id=getattr(request.state, "correlation_id", None),
            )
        )
        commit_audit_intent(repository, health)
    except Exception as exc:
        session = getattr(repository, "session", None)
        if session is not None:
            session.rollback()
        health.mark_unhealthy()
        raise V2ApiError(503, "audit_unavailable", "audit storage is unavailable") from exc
    return event


def _failure(event, repository, health, code: str, *, dispatch_state: str) -> None:
    repository.finalize(
        event.id,
        result="failed",
        dispatch_state=dispatch_state,
        failure_category=code,
    )
    commit_audit_outcome(repository, health)


def _success(event, repository, health, *, dispatch_state: str) -> None:
    repository.finalize(
        event.id,
        result="success",
        dispatch_state=dispatch_state,
    )
    commit_audit_outcome(repository, health)
