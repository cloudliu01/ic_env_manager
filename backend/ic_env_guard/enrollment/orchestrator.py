import asyncio
import hashlib
import hmac
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any
from uuid import UUID, uuid4

from ic_env_guard.agents.terminal_proxy import GatewayTicketStore
from ic_env_guard.enrollment.agent_client import (
    EnrollmentAgentClient,
    EnrollmentValidation,
    EnrollmentValidationError,
)
from ic_env_guard.enrollment.credential_store import CredentialStore, CredentialStoreError
from ic_env_guard.enrollment.jobs import (
    EnrollmentConflict,
    EnrollmentJobRequest,
    EnrollmentJobs,
    job_input_fingerprint,
)
from ic_env_guard.enrollment.ssh import (
    EnrollmentHelperResult,
    SshEnrollmentAdapter,
    SshEnrollmentError,
    SshEnrollmentRequest,
    parse_submitted_helper_result,
)
from ic_env_guard.fleet.models import (
    AgentRecord,
    EnrollmentJob,
    EnrollmentMethod,
    EnrollmentState,
    RegistryConflict,
    RegistryError,
    RevisionConflict,
)
from ic_env_guard.fleet.target_policy import ValidatedTarget
from ic_env_guard.fleet.transport import TransportProfile
from ic_env_guard.storage.enrollment_journal import EnrollmentJournalRepository
from ic_env_guard.storage.removal_journal import AgentRemovalRepository

PHASES = (
    "network",
    "ssh",
    "transport",
    "authentication",
    "protocol",
    "identity",
    "capabilities",
    "readiness",
)
_PUBLIC_STATE = {
    EnrollmentState.CREDENTIAL_ISSUED: "verifying",
    EnrollmentState.ACTIVATION_REQUESTED: "running",
    EnrollmentState.ACTIVATED: "running",
}


@dataclass(frozen=True)
class LegacyValidationRequest:
    base_url: str
    transport_profile_id: str


@dataclass(frozen=True)
class AutoEnrollmentAuditContext:
    actor_id: str | None
    source_addr: str | None
    correlation_id: str | None


@dataclass(frozen=True)
class CliSubmissionClaim:
    job: EnrollmentJob
    target: ValidatedTarget | None
    input_fingerprint: str
    audit_event_id: Any
    nonce: str
    already_accepted: bool = False


@dataclass(frozen=True)
class EnrollmentPublicResult:
    job: EnrollmentJob
    validation: EnrollmentValidation | None = None

    def to_public_dict(self) -> dict[str, Any]:
        validation = self.validation
        state = self.job.state
        phases: dict[str, dict[str, str | None]] = {}
        for name in PHASES:
            status = "pending"
            code = None
            if validation is not None:
                status = "success"
                if (
                    name == "ssh"
                    and self.job.enrollment_method is EnrollmentMethod.LEGACY_ADMIN_TOKEN
                ):
                    status = "not_applicable"
                elif name == "identity" and validation.instance_id is None:
                    status, code = "warning", "legacy_identity_unavailable"
                elif name == "readiness" and validation.readiness_warning:
                    status, code = "warning", validation.readiness_warning
            elif state in {
                EnrollmentState.CANCELLED,
                EnrollmentState.EXPIRED,
                EnrollmentState.FAILED,
            }:
                status = "skipped"
            phases[name] = {"status": status, "code": code}
        agent = None
        if validation is not None:
            agent = {
                "agent_id": self.job.enrollment_id,
                "instance_id": validation.instance_id,
                "api_version": validation.api_version,
                "agent_version": validation.agent_version,
                "capabilities": list(validation.capabilities),
            }
        return {
            "enrollment_id": self.job.enrollment_id,
            "state": _PUBLIC_STATE.get(state, state.value),
            "expires_at": self.job.expires_at.isoformat().replace("+00:00", "Z"),
            "last_error_code": self.job.last_error_code,
            "preview": {"agent": agent, "phases": phases},
        }


@dataclass(frozen=True)
class _LeasedOutcome:
    job: EnrollmentJob
    value: Any = None
    error: Exception | None = None


class _AutoStateLost(Exception):
    pass


class MutationSagaError(EnrollmentConflict):
    def __init__(
        self, code: str, *, dispatch_state: str, recoverable: bool = False
    ) -> None:
        super().__init__(code)
        self.dispatch_state = dispatch_state
        self.recoverable = recoverable


class EnrollmentOrchestrator:
    def __init__(
        self,
        *,
        jobs: EnrollmentJobs,
        journal: EnrollmentJournalRepository,
        credential_store: CredentialStore,
        agent_client: EnrollmentAgentClient | None,
        registry: Any,
        clock: Callable[[], datetime] | None = None,
        ssh_adapter: SshEnrollmentAdapter | None = None,
        service_key_adapter: SshEnrollmentAdapter | None = None,
        service_key_configured: bool = False,
        transport_profiles: tuple[TransportProfile, ...] = (),
        auto_audit: Any | None = None,
        removal_repository: AgentRemovalRepository | None = None,
        terminal_usage: GatewayTicketStore | None = None,
    ) -> None:
        self.jobs = jobs
        self.journal = journal
        self.credential_store = credential_store
        self.agent_client = agent_client
        self.registry = registry
        self._mutation_failures: dict[str, tuple[str, str]] = {}
        self._clock = clock or (lambda: datetime.now(UTC))
        self.ssh_adapter = ssh_adapter
        self.service_key_adapter = service_key_adapter
        self._service_key_configured = service_key_configured
        self._transport_profiles = {
            profile.id: profile for profile in transport_profiles
        }
        self._auto_audit = auto_audit
        self._removals = removal_repository
        self._terminal_usage = terminal_usage
        self._background_tasks: dict[str, asyncio.Task[None]] = {}
        self._closing = False
        self._validation_cache: dict[str, EnrollmentValidation] = {}
        self._recovery_owner = str(uuid4())
        max_operation = getattr(agent_client, "max_network_operation_seconds", 10.0)
        self._recovery_lease_seconds = max(3, math.ceil(float(max_operation) * 2))

    def create(self, request: EnrollmentJobRequest) -> EnrollmentPublicResult:
        return EnrollmentPublicResult(self.jobs.create(request, now=self._clock()))

    def create_auto(
        self,
        request: EnrollmentJobRequest,
        audit_context: AutoEnrollmentAuditContext,
    ) -> EnrollmentPublicResult:
        method = (
            EnrollmentMethod.SSH_SERVICE_KEY
            if self._service_key_configured
            else EnrollmentMethod.SSH_AUTO
        )
        request = replace(request, enrollment_method=method)
        pending = self.jobs.create(request, now=self._clock())
        adapter = (
            self.service_key_adapter
            if method is EnrollmentMethod.SSH_SERVICE_KEY
            else self.ssh_adapter
        )
        if self._closing or adapter is None or not adapter.healthy:
            awaiting = self.journal.replace_if_state(
                replace(
                    pending,
                    state=EnrollmentState.AWAITING_CLI,
                    enrollment_method=EnrollmentMethod.SSH_CLI,
                    last_error_code="ssh_unavailable",
                    updated_at=self._clock(),
                ),
                expected_state=EnrollmentState.PENDING,
            )
            return EnrollmentPublicResult(awaiting)
        running = self.journal.claim_pending_auto(
            pending.enrollment_id, now=self._clock()
        )
        if running is None:
            current = self.journal.get(pending.enrollment_id)
            if current is None:
                raise RegistryError("enrollment journal storage is unavailable")
            return EnrollmentPublicResult(current)
        self._schedule_auto(running, audit_context)
        return EnrollmentPublicResult(running)

    def start_rotation(
        self,
        agent_id: str,
        *,
        ssh_user: str,
        ssh_host: str,
        ssh_port: int,
        audit_context: AutoEnrollmentAuditContext | None = None,
    ) -> EnrollmentPublicResult:
        method = (
            EnrollmentMethod.SSH_SERVICE_KEY
            if self._service_key_configured
            else EnrollmentMethod.SSH_AUTO
        )
        pending = self.jobs.create_rotation(
            EnrollmentJobRequest(
                normalized_endpoint="rotation-captured",
                transport_profile_id="rotation-captured",
                ssh_user=ssh_user,
                ssh_host=ssh_host,
                ssh_port=ssh_port,
                enrollment_method=method,
                replace_agent_id=agent_id,
            ),
            now=self._clock(),
        )
        adapter = (
            self.service_key_adapter
            if method is EnrollmentMethod.SSH_SERVICE_KEY
            else self.ssh_adapter
        )
        if self._closing or adapter is None or not adapter.healthy:
            awaiting = self.journal.replace_if_state(
                replace(
                    pending,
                    state=EnrollmentState.AWAITING_CLI,
                    enrollment_method=EnrollmentMethod.SSH_CLI,
                    last_error_code="ssh_unavailable",
                    updated_at=self._clock(),
                ),
                expected_state=EnrollmentState.PENDING,
            )
            return EnrollmentPublicResult(awaiting)
        running = self.journal.claim_pending_auto(
            pending.enrollment_id, now=self._clock()
        )
        if running is None:
            current = self.journal.get(pending.enrollment_id)
            if current is None:
                raise RegistryError("enrollment journal storage is unavailable")
            return EnrollmentPublicResult(current)
        self._schedule_auto(
            running,
            audit_context or AutoEnrollmentAuditContext(None, None, None),
        )
        return EnrollmentPublicResult(running)

    @property
    def background_task_count(self) -> int:
        return len(self._background_tasks)

    async def wait_for_background(self) -> None:
        while self._background_tasks:
            await asyncio.gather(
                *tuple(self._background_tasks.values()), return_exceptions=True
            )

    async def shutdown(self) -> None:
        self._closing = True
        tasks = tuple(self._background_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def get(self, enrollment_id: str) -> EnrollmentPublicResult:
        return EnrollmentPublicResult(
            self.jobs.get(enrollment_id, now=self._clock()),
            self._validation_cache.get(enrollment_id),
        )

    async def consume(
        self,
        enrollment_id: str,
        *,
        display_name: str,
        input_fingerprint: str,
    ) -> AgentRecord:
        current = self.jobs.get(enrollment_id, now=self._clock())
        if current.state is EnrollmentState.CONSUMED:
            raise EnrollmentConflict("agent_enrollment_consumed")
        if current.state is EnrollmentState.EXPIRED:
            raise EnrollmentConflict("agent_enrollment_expired")
        if current.state is not EnrollmentState.VERIFIED:
            raise EnrollmentConflict("agent_enrollment_not_verified")
        if input_fingerprint != job_input_fingerprint(current):
            raise EnrollmentConflict("agent_enrollment_input_changed")
        duplicate = self.registry.find_duplicate(
            instance_id=current.remote_instance_id,
            normalized_endpoint=current.normalized_endpoint,
        )
        if duplicate is not None:
            raise EnrollmentConflict("agent_already_registered")
        requested = self.jobs.consume(
            enrollment_id,
            display_name=display_name,
            input_fingerprint=input_fingerprint,
            now=self._clock(),
        )
        await self.recover()
        current = self.journal.get(requested.enrollment_id)
        record = self.registry.get(requested.enrollment_id)
        if current is None or current.state is not EnrollmentState.CONSUMED or record is None:
            code = current.last_error_code if current is not None else None
            failure = self._mutation_failures.pop(enrollment_id, None)
            if failure is not None:
                raise MutationSagaError(failure[0], dispatch_state=failure[1])
            raise MutationSagaError(
                code or "agent_enrollment_activation_pending",
                dispatch_state="unknown",
            )
        return record

    async def consume_rotation(
        self, agent_id: str, enrollment_id: str
    ) -> AgentRecord:
        current = self.jobs.get(enrollment_id, now=self._clock())
        registered = self.registry.get(agent_id)
        if registered is None:
            raise EnrollmentConflict("agent_not_found")
        if current.replace_agent_id != agent_id:
            raise EnrollmentConflict("agent_enrollment_conflict")
        if (
            current.old_enrollment_method is not EnrollmentMethod.LEGACY_ADMIN_TOKEN
            and current.remote_instance_id != current.old_instance_id
        ):
            raise MutationSagaError(
                "agent_identity_changed", dispatch_state="not_dispatched"
            )
        requested = self.jobs.consume_rotation(
            enrollment_id,
            agent_id=agent_id,
            display_name=current.old_display_name or registered.display_name,
            now=self._clock(),
        )
        await self.recover()
        finished = self.journal.get(requested.enrollment_id)
        rotated = self.registry.get(agent_id)
        if (
            finished is None
            or finished.state is not EnrollmentState.CONSUMED
            or rotated is None
        ):
            code = finished.last_error_code if finished is not None else None
            failure = self._mutation_failures.pop(requested.enrollment_id, None)
            if failure is not None:
                raise MutationSagaError(
                    failure[0], dispatch_state=failure[1]
                )
            raise MutationSagaError(
                code or "agent_enrollment_activation_pending",
                dispatch_state="unknown",
            )
        return rotated

    async def update_agent(
        self,
        agent_id: str,
        *,
        display_name: str | None = None,
        enabled: bool | None = None,
        base_url: str | None = None,
        transport_profile_id: str | None = None,
    ) -> AgentRecord:
        current = self.registry.get(agent_id)
        if current is None:
            raise EnrollmentConflict("agent_not_found")
        endpoint = base_url or current.normalized_endpoint
        profile = transport_profile_id or current.transport_profile_id
        target_changed = (
            endpoint != current.normalized_endpoint
            or profile != current.transport_profile_id
        )
        if target_changed:
            if current.instance_id is None:
                raise EnrollmentConflict("legacy_revalidation_required")
            if self.agent_client is None:
                raise EnrollmentConflict("agent_validation_unavailable")
            try:
                target = self.agent_client.prepare(endpoint, profile)
                token = self.credential_store.read(current.credential_ref)
                validation = await self.agent_client.validate_pending(
                    target, token, helper_instance_id=current.instance_id
                )
            except EnrollmentValidationError as exc:
                raise MutationSagaError(
                    exc.code, dispatch_state=exc.dispatch_state
                ) from exc
            except CredentialStoreError as exc:
                raise MutationSagaError(
                    "agent_credential_unavailable", dispatch_state="not_dispatched"
                ) from exc
            if validation.instance_id != current.instance_id:
                raise MutationSagaError(
                    "agent_identity_changed", dispatch_state="dispatched"
                )
            endpoint = validation.normalized_endpoint
        candidate = replace(
            current,
            display_name=display_name or current.display_name,
            enabled=current.enabled if enabled is None else enabled,
            normalized_endpoint=endpoint,
            transport_profile_id=profile,
        )
        try:
            return self.registry.update_and_reset_status(
                candidate,
                expected_revision=current.revision,
                now=self._clock(),
            )
        except RevisionConflict as exc:
            raise MutationSagaError(
                "agent_changed",
                dispatch_state="dispatched" if target_changed else "not_dispatched",
            ) from exc
        except RegistryConflict as exc:
            if str(exc) == "agent_mutation_in_progress":
                code = "agent_mutation_in_progress"
            else:
                code = "agent_already_registered"
            raise MutationSagaError(
                code,
                dispatch_state="dispatched" if target_changed else "not_dispatched",
            ) from exc
        except RegistryError as exc:
            raise MutationSagaError(
                "agent_registry_unavailable",
                dispatch_state="dispatched" if target_changed else "not_dispatched",
            ) from exc

    async def remove_agent(
        self,
        agent_id: str,
        *,
        audit_event_id: int,
        local_only: bool,
    ) -> None:
        if self._removals is None:
            raise EnrollmentConflict("agent_removal_unavailable")
        if self._terminal_usage is not None and not self._terminal_usage.begin_removal(
            agent_id
        ):
            raise EnrollmentConflict("agent_in_use")
        try:
            try:
                job = self._removals.create_for_agent(
                    agent_id,
                    audit_event_id=audit_event_id,
                    local_only=local_only,
                    now=self._clock(),
                )
            except RegistryConflict as exc:
                raise EnrollmentConflict(str(exc)) from exc
            except RegistryError as exc:
                raise EnrollmentConflict("agent_removal_unavailable") from exc
            try:
                await self._resume_removal(job)
            except RegistryError as exc:
                raise EnrollmentConflict("agent_registry_unavailable") from exc
        finally:
            if self._terminal_usage is not None:
                self._terminal_usage.abort_removal(agent_id)

    def removal_is_recoverable(self, audit_event_id: int) -> bool:
        return self._removals is not None and self._removals.audit_is_recoverable(
            audit_event_id
        )

    async def _resume_removal(self, job: Any) -> None:
        if self._removals is None:
            raise EnrollmentConflict("agent_removal_unavailable")
        current = job
        if current.phase in {"pending", "revoking", "residual"}:
            if (
                not current.local_only
                and current.enrollment_method is not EnrollmentMethod.LEGACY_ADMIN_TOKEN
            ):
                if self.agent_client is None:
                    raise EnrollmentConflict("agent_removal_unavailable")
                if current.phase != "revoking":
                    current = self._removals.transition(
                        current, "revoking", now=self._clock()
                    )
                try:
                    target = self.agent_client.prepare(
                        current.normalized_endpoint,
                        current.transport_profile_id,
                    )
                    token = self.credential_store.read(current.credential_ref)
                    await self.agent_client.revoke(
                        target,
                        token,
                        credential_id=current.remote_credential_id,
                    )
                except EnrollmentValidationError as exc:
                    self._removals.transition(
                        current,
                        "residual",
                        now=self._clock(),
                        last_error_code=exc.code,
                    )
                    raise MutationSagaError(
                        exc.code,
                        dispatch_state=exc.dispatch_state,
                        recoverable=True,
                    ) from exc
                except CredentialStoreError as exc:
                    self._removals.transition(
                        current,
                        "residual",
                        now=self._clock(),
                        last_error_code="agent_credential_unavailable",
                    )
                    raise MutationSagaError(
                        "agent_credential_unavailable",
                        dispatch_state="not_dispatched",
                        recoverable=True,
                    ) from exc
            current = self._removals.transition(current, "revoked", now=self._clock())
        if current.phase == "revoked":
            deleted = self.registry.delete_if_revision_and_credential(
                current.agent_id,
                expected_revision=current.captured_revision,
                expected_credential_ref=current.credential_ref,
                owner_removal_id=current.removal_id,
            )
            if not deleted:
                existing = self.registry.get(current.agent_id)
                if existing is not None:
                    self._removals.transition(
                        current,
                        "residual",
                        now=self._clock(),
                        last_error_code="agent_changed",
                    )
                    raise MutationSagaError(
                        "agent_changed",
                        dispatch_state=(
                            "not_dispatched" if current.local_only else "dispatched"
                        ),
                        recoverable=True,
                    )
            current = self._removals.transition(
                current, "registry_deleted", now=self._clock()
            )
        if current.phase == "registry_deleted":
            with self.credential_store.lifecycle_lease():
                try:
                    self.credential_store.delete_if_exists(current.credential_ref)
                except CredentialStoreError as exc:
                    self._removals.transition(
                        current,
                        "residual",
                        now=self._clock(),
                        last_error_code="credential_cleanup_failed",
                    )
                    raise MutationSagaError(
                        "credential_cleanup_failed",
                        dispatch_state=(
                            "not_dispatched" if current.local_only else "dispatched"
                        ),
                        recoverable=True,
                    ) from exc
            current = self._removals.transition(
                current, "credential_deleted", now=self._clock()
            )
        if current.phase == "credential_deleted":
            self._removals.transition(current, "completed", now=self._clock())

    async def recover_removals(self) -> None:
        if self._removals is None:
            return
        self._removals.finalize_orphaned_pending_audits()
        for job in self._removals.list_recoverable():
            if self._terminal_usage is not None and not self._terminal_usage.begin_removal(
                job.agent_id
            ):
                continue
            try:
                try:
                    await self._resume_removal(job)
                except EnrollmentConflict:
                    continue
                self._removals.finalize_audit_if_pending(job, success=True)
            finally:
                if self._terminal_usage is not None:
                    self._terminal_usage.finish_removal(job.agent_id)

    async def cancel(self, enrollment_id: str) -> EnrollmentPublicResult:
        task = self._background_tasks.get(enrollment_id)
        if task is not None:
            task.cancel()
        with self.credential_store.lifecycle_lease():
            return EnrollmentPublicResult(
                self._cleanup_terminal(
                    self.jobs.cancel(enrollment_id, now=self._clock())
                )
            )

    def begin_cli_submission(
        self,
        *,
        enrollment_id: str,
        ssh_user: str,
        ssh_host: str,
        ssh_port: int,
        pinned_address: str,
        peer_uid: int,
        resume_nonce: str | None = None,
        context: AutoEnrollmentAuditContext,
    ) -> CliSubmissionClaim:
        if self._closing or self.agent_client is None or self._auto_audit is None:
            raise EnrollmentValidationError(
                "enrollment_unavailable", dispatch_state="not_dispatched"
            )
        try:
            record_intent = getattr(self._auto_audit, "record_cli_intent", None)
            if record_intent is None:
                record_intent = self._auto_audit.record_intent
            event_id = record_intent(enrollment_id, context)
        except Exception:
            raise EnrollmentValidationError(
                "audit_unavailable", dispatch_state="not_dispatched"
            ) from None
        try:
            job = self.jobs.get(enrollment_id, now=self._clock())
            if (job.ssh_user, job.ssh_host, job.ssh_port) != (
                ssh_user,
                ssh_host,
                ssh_port,
            ):
                raise EnrollmentValidationError(
                    "agent_enrollment_input_changed", dispatch_state="not_dispatched"
                )
            expected_fingerprint = job_input_fingerprint(
                replace(job, enrollment_method=EnrollmentMethod.SSH_CLI)
            )
            if resume_nonce is not None:
                try:
                    parsed_nonce = UUID(resume_nonce)
                    parsed_pin = ip_address(pinned_address)
                except (TypeError, ValueError):
                    raise EnrollmentValidationError(
                        "agent_enrollment_conflict", dispatch_state="not_dispatched"
                    ) from None
                if (
                    str(parsed_nonce) != resume_nonce
                    or str(parsed_pin) != pinned_address
                    or not isinstance(peer_uid, int)
                    or isinstance(peer_uid, bool)
                    or peer_uid < 0
                ):
                    raise EnrollmentValidationError(
                        "agent_enrollment_conflict", dispatch_state="not_dispatched"
                    )
                if job.state is EnrollmentState.CONSUMED:
                    candidate = _cli_accept_receipt(
                        enrollment_id=job.enrollment_id,
                        nonce=resume_nonce,
                        peer_uid=peer_uid,
                        input_fingerprint=expected_fingerprint,
                        pinned_address=pinned_address,
                    )
                    if job.cli_accept_receipt is not None and hmac.compare_digest(
                        job.cli_accept_receipt, candidate
                    ):
                        return CliSubmissionClaim(
                            job=job,
                            target=None,
                            input_fingerprint=expected_fingerprint,
                            audit_event_id=event_id,
                            nonce=resume_nonce,
                            already_accepted=True,
                        )
                    raise EnrollmentValidationError(
                        "agent_enrollment_conflict", dispatch_state="not_dispatched"
                    )
                durable_match = (
                    job.enrollment_method is EnrollmentMethod.SSH_CLI
                    and job.cli_resume_nonce == resume_nonce
                    and job.cli_peer_uid == peer_uid
                    and job.cli_input_fingerprint == expected_fingerprint
                    and job.cli_pinned_address == pinned_address
                )
                if not durable_match:
                    raise EnrollmentValidationError(
                        "agent_enrollment_conflict", dispatch_state="not_dispatched"
                    )
                target = self.agent_client.prepare_pinned(
                    job.normalized_endpoint,
                    job.transport_profile_id,
                    pinned_address,
                )
                if job.state in {
                    EnrollmentState.CREDENTIAL_ISSUED,
                    EnrollmentState.VERIFYING,
                    EnrollmentState.VERIFIED,
                    EnrollmentState.ACTIVATION_REQUESTED,
                    EnrollmentState.ACTIVATED,
                }:
                    return CliSubmissionClaim(
                        job=job,
                        target=target,
                        input_fingerprint=expected_fingerprint,
                        audit_event_id=event_id,
                        nonce=resume_nonce,
                        already_accepted=True,
                    )
                if (
                    job.state is EnrollmentState.RUNNING
                    and job.recovery_owner == resume_nonce
                    and job.recovery_lease_until is not None
                    and job.recovery_lease_until > self._clock()
                ):
                    return CliSubmissionClaim(
                        job=job,
                        target=target,
                        input_fingerprint=expected_fingerprint,
                        audit_event_id=event_id,
                        nonce=resume_nonce,
                    )
                raise EnrollmentValidationError(
                    "agent_enrollment_conflict", dispatch_state="not_dispatched"
                )
            if job.state is not EnrollmentState.AWAITING_CLI:
                raise EnrollmentValidationError(
                    "agent_enrollment_conflict", dispatch_state="not_dispatched"
                )
            target = self.agent_client.prepare_cli_target(
                job.normalized_endpoint,
                job.transport_profile_id,
                ssh_host=ssh_host,
                ssh_port=ssh_port,
                pinned_address=pinned_address,
            )
            nonce = str(uuid4())
            claimed = self.journal.replace_if_state(
                replace(
                    job,
                    state=EnrollmentState.RUNNING,
                    enrollment_method=EnrollmentMethod.SSH_CLI,
                    recovery_owner=nonce,
                    recovery_lease_until=job.expires_at,
                    recovery_revision=job.recovery_revision + 1,
                    cli_resume_nonce=nonce,
                    cli_peer_uid=peer_uid,
                    cli_input_fingerprint=expected_fingerprint,
                    cli_pinned_address=pinned_address,
                    updated_at=self._clock(),
                ),
                expected_state=EnrollmentState.AWAITING_CLI,
            )
            return CliSubmissionClaim(
                job=claimed,
                target=target,
                input_fingerprint=expected_fingerprint,
                audit_event_id=event_id,
                nonce=nonce,
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, EnrollmentValidationError) else "storage_unavailable"
            self._record_auto_outcome(
                event_id,
                result="failure",
                dispatch_state="not_dispatched",
                failure_category=code,
            )
            raise

    async def complete_cli_submission(
        self,
        claim: CliSubmissionClaim,
        *,
        helper_payload: bytes,
        input_fingerprint: str,
        nonce: str,
    ) -> EnrollmentPublicResult:
        try:
            return await self._complete_cli_submission(
                claim,
                helper_payload=helper_payload,
                input_fingerprint=input_fingerprint,
                nonce=nonce,
            )
        except Exception as exc:
            code = getattr(exc, "code", "ssh_remote_command_failed")
            self._record_auto_outcome(
                claim.audit_event_id,
                result="failure",
                dispatch_state="dispatched",
                failure_category=code,
            )
            raise

    async def _complete_cli_submission(
        self,
        claim: CliSubmissionClaim,
        *,
        helper_payload: bytes,
        input_fingerprint: str,
        nonce: str,
    ) -> EnrollmentPublicResult:
        if nonce != claim.nonce or input_fingerprint != claim.input_fingerprint:
            raise EnrollmentValidationError(
                "agent_enrollment_input_changed", dispatch_state="dispatched"
            )
        now = self._clock()
        current = self.journal.recheck_cli_submission(
            claim.job.enrollment_id,
            owner=claim.nonce,
            expected_revision=claim.job.recovery_revision,
            now=now,
        )
        if current is None:
            latest = self.journal.get(claim.job.enrollment_id)
            code = (
                "agent_enrollment_expired"
                if latest is not None and latest.state is EnrollmentState.EXPIRED
                else "agent_enrollment_conflict"
            )
            raise EnrollmentValidationError(code, dispatch_state="dispatched")
        request = SshEnrollmentRequest(
            manager_id=current.manager_id,
            enrollment_id=current.enrollment_id,
            base_url=current.normalized_endpoint,
            ssh_user=current.ssh_user or "",
            ssh_host=current.ssh_host or "",
            ssh_port=current.ssh_port or 0,
            expires_at=current.expires_at,
        )
        if claim.target is None:
            raise EnrollmentValidationError(
                "agent_enrollment_conflict", dispatch_state="dispatched"
            )
        helper = parse_submitted_helper_result(
            helper_payload,
            request=request,
            validation_target=claim.target,
            now=now,
        )
        await self._publish_cli_helper(current, helper)
        self._record_auto_outcome(
            claim.audit_event_id, result="success", dispatch_state="dispatched"
        )
        return self.get(claim.job.enrollment_id)

    def release_cli_connection(
        self, claim: CliSubmissionClaim, *, result_received: bool, code: str
    ) -> None:
        if code == "already_accepted":
            self._record_auto_outcome(
                claim.audit_event_id,
                result="success",
                dispatch_state="not_dispatched",
            )
            return
        self._record_auto_outcome(
            claim.audit_event_id,
            result="failure",
            dispatch_state="dispatched" if result_received else "unknown",
            failure_category=code,
        )

    async def validate_legacy(
        self, request: LegacyValidationRequest, token: str
    ) -> EnrollmentPublicResult:
        if self.agent_client is None:
            raise EnrollmentValidationError(
                "enrollment_unavailable", dispatch_state="not_dispatched"
            )
        target = self.agent_client.prepare(
            request.base_url, request.transport_profile_id
        )
        job_request = EnrollmentJobRequest(
            normalized_endpoint=target.normalized_endpoint,
            transport_profile_id=request.transport_profile_id,
            enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
        )
        job = self.jobs.create(job_request, now=self._clock())
        reference = None
        try:
            with self.credential_store.lifecycle_lease():
                reference = self.credential_store.put(token.encode("utf-8"))
                issued = replace(
                    job,
                    state=EnrollmentState.RUNNING,
                    updated_at=self._clock(),
                )
                issued = self.journal.replace_if_state(
                    issued, expected_state=EnrollmentState.PENDING
                )
                issued = self.journal.replace_if_state(
                    replace(
                        issued,
                        state=EnrollmentState.CREDENTIAL_ISSUED,
                        credential_temp_ref=reference,
                        updated_at=self._clock(),
                    ),
                    expected_state=EnrollmentState.RUNNING,
                )
            verifying = self.journal.replace_if_state(
                replace(issued, state=EnrollmentState.VERIFYING, updated_at=self._clock()),
                expected_state=EnrollmentState.CREDENTIAL_ISSUED,
            )
            validation = await self.agent_client.validate_legacy(
                target, self.credential_store.read(reference)
            )
            verified = self.journal.replace_if_state(
                replace(verifying, state=EnrollmentState.VERIFIED, updated_at=self._clock()),
                expected_state=EnrollmentState.VERIFYING,
            )
            self._validation_cache[verified.enrollment_id] = validation
            return EnrollmentPublicResult(verified, validation)
        except (EnrollmentValidationError, CredentialStoreError) as exc:
            current = self.journal.get(job.enrollment_id)
            if current is not None and not current.state.terminal:
                try:
                    failed = self.journal.replace_if_state(
                        replace(
                            current,
                            state=EnrollmentState.FAILED,
                            last_error_code=(
                                exc.code
                                if isinstance(exc, EnrollmentValidationError)
                                else "credential_store_unavailable"
                            ),
                            updated_at=self._clock(),
                        ),
                        expected_state=current.state,
                    )
                    self._cleanup_terminal(failed)
                except Exception:
                    pass
            if isinstance(exc, CredentialStoreError):
                raise EnrollmentValidationError(
                    "credential_store_unavailable", dispatch_state="not_dispatched"
                ) from exc
            raise

    async def recover(self) -> None:
        for enrollment_id in self.journal.prepare_recovery(now=self._clock()):
            job = self.journal.claim_recovery(
                enrollment_id,
                owner=self._recovery_owner,
                now=self._clock(),
                lease_seconds=self._recovery_lease_seconds,
            )
            if job is None:
                continue
            try:
                if job.state in {
                    EnrollmentState.ACTIVATION_REQUESTED,
                    EnrollmentState.ACTIVATED,
                } and job.save_requested:
                    await self._recover_activation(job)
                elif job.state in {
                    EnrollmentState.CREDENTIAL_ISSUED,
                    EnrollmentState.VERIFYING,
                    EnrollmentState.VERIFIED,
                }:
                    await self._recover_validation(job)
            except (RevisionConflict, RegistryError):
                # Another recovery owner won the durable fence. Reloading is enough to
                # converge because every remotely visible operation is idempotent.
                self._reload_after_fence_loss(enrollment_id)
        for terminal in self.journal.list_terminal_cleanup():
            self._cleanup_terminal(terminal)

    def _schedule_auto(
        self, job: EnrollmentJob, context: AutoEnrollmentAuditContext
    ) -> None:
        if self._closing or job.enrollment_id in self._background_tasks:
            return
        task = asyncio.create_task(self._run_auto(job, context))
        self._background_tasks[job.enrollment_id] = task
        task.add_done_callback(
            lambda completed, enrollment_id=job.enrollment_id: self._background_done(
                enrollment_id, completed
            )
        )

    def _background_done(
        self, enrollment_id: str, task: asyncio.Task[None]
    ) -> None:
        if self._background_tasks.get(enrollment_id) is task:
            self._background_tasks.pop(enrollment_id, None)
        if not task.cancelled():
            task.exception()

    async def _run_auto(
        self, job: EnrollmentJob, context: AutoEnrollmentAuditContext
    ) -> None:
        event_id = None
        audit = self._auto_audit
        try:
            if audit is None:
                self._converge_auto(job.enrollment_id, "audit_unavailable")
                return
            try:
                event_id = audit.record_intent(job.enrollment_id, context)
            except Exception:
                self._converge_auto(job.enrollment_id, "audit_unavailable")
                return
            dispatch_job = self.journal.recheck_auto_dispatch(
                job.enrollment_id, now=self._clock()
            )
            if dispatch_job is None:
                self._record_auto_outcome(
                    event_id,
                    result="failure",
                    dispatch_state="not_dispatched",
                    failure_category="agent_enrollment_cancelled",
                )
                return
            await self._issue_and_validate_auto(dispatch_job)
        except asyncio.CancelledError:
            if self._closing:
                raise
            self._record_auto_outcome(
                event_id,
                result="failure",
                dispatch_state="unknown",
                failure_category="agent_enrollment_cancelled",
            )
            raise
        except _AutoStateLost:
            if self._closing:
                return
            self._record_auto_outcome(
                event_id,
                result="failure",
                dispatch_state="unknown",
                failure_category="agent_enrollment_cancelled",
            )
            return
        except SshEnrollmentError as exc:
            state = (
                EnrollmentState.AWAITING_CLI
                if exc.code == "ssh_interaction_required"
                else EnrollmentState.FAILED
            )
            self._converge_auto(job.enrollment_id, exc.code, state=state)
            self._record_auto_outcome(
                event_id,
                result="failure",
                dispatch_state=exc.dispatch_state,
                failure_category=exc.code,
            )
            return
        except CredentialStoreError:
            self._converge_auto(job.enrollment_id, "credential_store_unavailable")
            self._record_auto_outcome(
                event_id,
                result="failure",
                dispatch_state="dispatched",
                failure_category="credential_store_unavailable",
            )
            return
        except EnrollmentValidationError as exc:
            self._record_auto_outcome(
                event_id,
                result="failure",
                dispatch_state=exc.dispatch_state,
                failure_category=exc.code,
            )
            return
        except RegistryError:
            self._converge_auto(job.enrollment_id, "storage_unavailable")
            self._record_auto_outcome(
                event_id,
                result="failure",
                dispatch_state="dispatched",
                failure_category="storage_unavailable",
            )
            return
        except Exception:
            current = self.journal.get(job.enrollment_id)
            if current is not None and current.state is EnrollmentState.RUNNING:
                self._converge_auto(job.enrollment_id, "ssh_remote_command_failed")
            self._record_auto_outcome(
                event_id,
                result="failure",
                dispatch_state="dispatched",
                failure_category="ssh_remote_command_failed",
            )
            return
        self._record_auto_outcome(
            event_id,
            result="success",
            dispatch_state="dispatched",
        )

    async def _issue_and_validate_auto(self, job: EnrollmentJob) -> None:
        adapter = (
            self.service_key_adapter
            if job.enrollment_method is EnrollmentMethod.SSH_SERVICE_KEY
            else self.ssh_adapter
        )
        if adapter is None or not job.ssh_user or not job.ssh_host or not job.ssh_port:
            raise SshEnrollmentError("ssh_unavailable")
        try:
            profile = self._transport_profiles[job.transport_profile_id]
        except KeyError:
            raise SshEnrollmentError("ssh_unavailable") from None
        helper = await adapter.issue(
            SshEnrollmentRequest(
                manager_id=job.manager_id,
                enrollment_id=job.enrollment_id,
                base_url=job.normalized_endpoint,
                ssh_user=job.ssh_user,
                ssh_host=job.ssh_host,
                ssh_port=job.ssh_port,
                expires_at=job.expires_at,
            ),
            profile,
        )
        if self._closing:
            raise _AutoStateLost
        reference = None
        try:
            with self.credential_store.lifecycle_lease():
                current = self.journal.get(job.enrollment_id)
                if current is None or current.state is not EnrollmentState.RUNNING:
                    raise _AutoStateLost
                reference = self.credential_store.put(helper.token)
                issued = self.journal.replace_if_state(
                    replace(
                        current,
                        state=EnrollmentState.CREDENTIAL_ISSUED,
                        remote_instance_id=helper.instance_id,
                        remote_credential_id=helper.credential_id,
                        credential_temp_ref=reference,
                        validated_http_address=str(
                            helper.validation_target.pinned_address
                        ),
                        updated_at=self._clock(),
                    ),
                    expected_state=EnrollmentState.RUNNING,
                )
        except Exception:
            if reference is not None:
                self.credential_store.delete_if_exists(reference)
            raise
        verifying = self.journal.replace_if_state(
            replace(
                issued,
                state=EnrollmentState.VERIFYING,
                updated_at=self._clock(),
            ),
            expected_state=EnrollmentState.CREDENTIAL_ISSUED,
        )
        if self.agent_client is None:
            raise EnrollmentValidationError(
                "enrollment_unavailable", dispatch_state="not_dispatched"
            )
        target = helper.validation_target
        if target is None:
            raise EnrollmentValidationError(
                "enrollment_unavailable", dispatch_state="not_dispatched"
            )
        validation = await self.agent_client.validate_pending(
            target,
            self.credential_store.read(verifying.credential_temp_ref),
            helper_instance_id=helper.instance_id,
        )
        verified = self.journal.replace_if_state(
            replace(
                verifying,
                state=EnrollmentState.VERIFIED,
                updated_at=self._clock(),
            ),
            expected_state=EnrollmentState.VERIFYING,
        )
        self._validation_cache[verified.enrollment_id] = validation

    async def _publish_cli_helper(
        self, job: EnrollmentJob, helper: EnrollmentHelperResult
    ) -> None:
        reference = None
        try:
            with self.credential_store.lifecycle_lease():
                current = self.journal.get(job.enrollment_id)
                if (
                    current is None
                    or current.state is not EnrollmentState.RUNNING
                    or current.enrollment_method is not EnrollmentMethod.SSH_CLI
                    or current.recovery_owner != job.recovery_owner
                    or current.recovery_revision != job.recovery_revision
                ):
                    raise _AutoStateLost
                reference = self.credential_store.put(helper.token)
                issued = self.journal.replace_if_state(
                    replace(
                        current,
                        state=EnrollmentState.CREDENTIAL_ISSUED,
                        remote_instance_id=helper.instance_id,
                        remote_credential_id=helper.credential_id,
                        credential_temp_ref=reference,
                        validated_http_address=str(
                            helper.validation_target.pinned_address
                        ),
                        recovery_owner=None,
                        recovery_lease_until=None,
                        recovery_revision=current.recovery_revision + 1,
                        updated_at=self._clock(),
                    ),
                    expected_state=EnrollmentState.RUNNING,
                    expected_recovery_owner=current.recovery_owner,
                    expected_recovery_revision=current.recovery_revision,
                    recovery_now=self._clock(),
                )
        except Exception:
            if reference is not None:
                self.credential_store.delete_if_exists(reference)
            raise
        verifying = self.journal.replace_if_state(
            replace(issued, state=EnrollmentState.VERIFYING, updated_at=self._clock()),
            expected_state=EnrollmentState.CREDENTIAL_ISSUED,
        )
        if self.agent_client is None:
            raise EnrollmentValidationError(
                "enrollment_unavailable", dispatch_state="not_dispatched"
            )
        validation = await self.agent_client.validate_pending(
            helper.validation_target,
            self.credential_store.read(verifying.credential_temp_ref),
            helper_instance_id=helper.instance_id,
        )
        verified = self.journal.replace_if_state(
            replace(verifying, state=EnrollmentState.VERIFIED, updated_at=self._clock()),
            expected_state=EnrollmentState.VERIFYING,
        )
        self._validation_cache[verified.enrollment_id] = validation

    def _converge_auto(
        self,
        enrollment_id: str,
        code: str,
        *,
        state: EnrollmentState = EnrollmentState.FAILED,
    ) -> None:
        current = self.journal.get(enrollment_id)
        if current is None or current.state is not EnrollmentState.RUNNING:
            return
        try:
            self.journal.replace_if_state(
                replace(
                    current,
                    state=state,
                    last_error_code=code,
                    updated_at=self._clock(),
                ),
                expected_state=EnrollmentState.RUNNING,
            )
        except (RegistryError, RevisionConflict):
            return

    def _record_auto_outcome(
        self,
        event_id: Any,
        *,
        result: str,
        dispatch_state: str,
        failure_category: str | None = None,
    ) -> None:
        if event_id is None or self._auto_audit is None:
            return
        try:
            self._auto_audit.record_outcome(
                event_id,
                result=result,
                dispatch_state=dispatch_state,
                failure_category=failure_category,
            )
        except Exception:
            return

    async def _recover_validation(self, job: EnrollmentJob) -> None:
        if self.agent_client is None:
            self._fail_claim(job, "enrollment_unavailable")
            return
        if not job.credential_temp_ref:
            self._fail_claim(job, "credential_store_unavailable")
            return
        if job.enrollment_method is EnrollmentMethod.LEGACY_ADMIN_TOKEN:
            self._fail_claim(job, "enrollment_recovery_unavailable")
            return
        if not job.validated_http_address:
            self._fail_claim(job, "target_address_forbidden")
            return
        current = job
        if current.state is EnrollmentState.CREDENTIAL_ISSUED:
            transitioned = self._transition_claimed(
                current, EnrollmentState.VERIFYING
            )
            if transitioned is None:
                return
            current = transitioned
        try:
            target = self.agent_client.prepare_pinned(
                current.normalized_endpoint,
                current.transport_profile_id,
                current.validated_http_address,
            )
            token = self.credential_store.read(current.credential_temp_ref)
        except EnrollmentValidationError as exc:
            self._fail_claim(current, exc.code)
            return
        except CredentialStoreError:
            self._fail_claim(current, "credential_store_unavailable")
            return
        if not current.remote_instance_id:
            self._fail_claim(current, "agent_identity_missing")
            return
        operation = self.agent_client.validate_pending(
            target, token, helper_instance_id=current.remote_instance_id
        )
        outcome = await self._run_with_recovery_lease(current, operation)
        if outcome is None:
            return
        current = outcome.job
        if outcome.error is not None:
            code = (
                outcome.error.code
                if isinstance(outcome.error, EnrollmentValidationError)
                else "agent_network_error"
            )
            self._fail_claim(current, code)
            return
        validation = outcome.value
        if current.state is EnrollmentState.VERIFIED:
            if self.journal.release_recovery_claim(
                current.enrollment_id,
                owner=self._recovery_owner,
                expected_revision=current.recovery_revision,
                now=self._clock(),
            ):
                self._validation_cache[current.enrollment_id] = validation
            return
        verified = self._transition_claimed(
            current,
            EnrollmentState.VERIFIED,
            clear_claim=True,
        )
        if verified is not None:
            self._validation_cache[verified.enrollment_id] = validation

    async def _run_with_recovery_lease(
        self, job: EnrollmentJob, operation: Any
    ) -> _LeasedOutcome | None:
        task = asyncio.create_task(operation)
        current = job
        interval = self._recovery_lease_seconds / 3
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=interval)
                if done:
                    error = None
                    value = None
                    try:
                        value = task.result()
                    except Exception as exc:  # the fenced caller persists a safe code
                        error = exc
                    renewed = self._renew_claim(current)
                    if renewed is None:
                        return None
                    return _LeasedOutcome(renewed, value, error)
                renewed = self._renew_claim(current)
                if renewed is None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return None
                current = renewed
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    def _renew_claim(self, job: EnrollmentJob) -> EnrollmentJob | None:
        try:
            return self.journal.renew_recovery_claim(
                job.enrollment_id,
                owner=self._recovery_owner,
                expected_revision=job.recovery_revision,
                now=self._clock(),
                lease_seconds=self._recovery_lease_seconds,
            )
        except RegistryError:
            return None

    def _transition_claimed(
        self,
        job: EnrollmentJob,
        state: EnrollmentState,
        *,
        clear_claim: bool = False,
    ) -> EnrollmentJob | None:
        now = self._clock()
        clear_cli = state in {
            EnrollmentState.CANCELLED,
            EnrollmentState.EXPIRED,
            EnrollmentState.FAILED,
            EnrollmentState.CONSUMED,
        }
        receipt = job.cli_accept_receipt
        if state is EnrollmentState.CONSUMED and all(
            value is not None
            for value in (
                job.cli_resume_nonce,
                job.cli_peer_uid,
                job.cli_input_fingerprint,
                job.cli_pinned_address,
            )
        ):
            receipt = _cli_accept_receipt(
                enrollment_id=job.enrollment_id,
                nonce=job.cli_resume_nonce or "",
                peer_uid=job.cli_peer_uid or 0,
                input_fingerprint=job.cli_input_fingerprint or "",
                pinned_address=job.cli_pinned_address or "",
            )
        elif state in {
            EnrollmentState.CANCELLED,
            EnrollmentState.EXPIRED,
            EnrollmentState.FAILED,
        }:
            receipt = None
        try:
            return self.journal.replace_if_state(
                replace(
                    job,
                    state=state,
                    updated_at=now,
                    recovery_owner=None if clear_claim else job.recovery_owner,
                    recovery_lease_until=(
                        None if clear_claim else job.recovery_lease_until
                    ),
                    recovery_revision=job.recovery_revision + 1,
                    cli_resume_nonce=None if clear_cli else job.cli_resume_nonce,
                    cli_peer_uid=None if clear_cli else job.cli_peer_uid,
                    cli_input_fingerprint=(
                        None if clear_cli else job.cli_input_fingerprint
                    ),
                    cli_pinned_address=None if clear_cli else job.cli_pinned_address,
                    cli_accept_receipt=receipt,
                ),
                expected_state=job.state,
                expected_recovery_owner=self._recovery_owner,
                expected_recovery_revision=job.recovery_revision,
                recovery_now=now,
            )
        except RevisionConflict:
            self._reload_after_fence_loss(job.enrollment_id)
            return None

    def _fail_claim(self, job: EnrollmentJob, code: str) -> None:
        try:
            self.journal.fail_recovery_claim(
                job.enrollment_id,
                owner=self._recovery_owner,
                expected_revision=job.recovery_revision,
                error_code=code,
                now=self._clock(),
            )
        except RegistryError:
            return

    def _residual_claim(self, job: EnrollmentJob, code: str) -> None:
        try:
            self.journal.release_recovery_residual(
                job.enrollment_id,
                owner=self._recovery_owner,
                expected_revision=job.recovery_revision,
                error_code=code,
                now=self._clock(),
            )
        except RegistryError:
            return

    def _record_mutation_failure(
        self, job: EnrollmentJob, code: str, dispatch_state: str
    ) -> None:
        self._mutation_failures[job.enrollment_id] = (code, dispatch_state)

    def _mark_claim_error(
        self, job: EnrollmentJob, code: str
    ) -> EnrollmentJob | None:
        now = self._clock()
        try:
            marked = self.journal.mark_recovery_claim_error(
                job.enrollment_id,
                owner=self._recovery_owner,
                expected_revision=job.recovery_revision,
                error_code=code,
                now=now,
            )
        except RegistryError:
            return None
        if not marked:
            self._reload_after_fence_loss(job.enrollment_id)
            return None
        return replace(
            job,
            last_error_code=code,
            recovery_revision=job.recovery_revision + 1,
            updated_at=now,
        )

    def _reload_after_fence_loss(self, enrollment_id: str) -> None:
        try:
            self.journal.get(enrollment_id)
        except RegistryError:
            return

    async def _recover_activation(self, job: EnrollmentJob) -> None:
        if job.replace_agent_id is not None:
            await self._recover_rotation(job)
            return
        if not job.credential_temp_ref or not job.requested_display_name:
            self._fail_claim(job, "credential_store_unavailable")
            return
        current = job
        target = None
        if current.enrollment_method is not EnrollmentMethod.LEGACY_ADMIN_TOKEN:
            if (
                self.agent_client is None
                or not current.remote_credential_id
                or not current.validated_http_address
            ):
                self._fail_claim(current, "enrollment_unavailable")
                return
            try:
                target = self.agent_client.prepare_pinned(
                    current.normalized_endpoint,
                    current.transport_profile_id,
                    current.validated_http_address,
                )
            except EnrollmentValidationError as exc:
                self._record_mutation_failure(current, exc.code, exc.dispatch_state)
                self._fail_claim(current, exc.code)
                return
        if current.state is EnrollmentState.ACTIVATION_REQUESTED:
            if current.enrollment_method is not EnrollmentMethod.LEGACY_ADMIN_TOKEN:
                try:
                    token = self.credential_store.read(current.credential_temp_ref)
                except CredentialStoreError:
                    self._fail_claim(current, "credential_store_unavailable")
                    return
                outcome = await self._run_with_recovery_lease(
                    current,
                    self.agent_client.activate(
                        target,
                        token,
                        enrollment_id=current.enrollment_id,
                        credential_id=current.remote_credential_id,
                    ),
                )
                if outcome is None:
                    return
                current = outcome.job
                if outcome.error is not None:
                    code = (
                        outcome.error.code
                        if isinstance(outcome.error, EnrollmentValidationError)
                        else "agent_network_error"
                    )
                    dispatch_state = (
                        outcome.error.dispatch_state
                        if isinstance(outcome.error, EnrollmentValidationError)
                        else "unknown"
                    )
                    self._record_mutation_failure(current, code, dispatch_state)
                    self._fail_claim(current, code)
                    return
            transitioned = self._transition_claimed(
                current, EnrollmentState.ACTIVATED
            )
            if transitioned is None:
                return
            current = transitioned
        renewed = self._renew_claim(current)
        if renewed is None:
            return
        current = renewed
        record = AgentRecord(
            agent_id=current.enrollment_id,
            instance_id=current.remote_instance_id,
            display_name=current.requested_display_name,
            normalized_endpoint=current.normalized_endpoint,
            credential_ref=current.credential_temp_ref,
            remote_credential_id=current.remote_credential_id,
            transport_profile_id=current.transport_profile_id,
            enrollment_method=current.enrollment_method,
            enabled=True,
            source="manual",
            revision=1,
            created_at=self._clock(),
            updated_at=self._clock(),
        )
        receipt = None
        if current.enrollment_method is EnrollmentMethod.SSH_CLI:
            receipt = _cli_accept_receipt(
                enrollment_id=current.enrollment_id,
                nonce=current.cli_resume_nonce or "",
                peer_uid=current.cli_peer_uid or 0,
                input_fingerprint=current.cli_input_fingerprint or "",
                pinned_address=current.cli_pinned_address or "",
            )
        compensate_conflict = False
        with self.credential_store.lifecycle_lease():
            commit = getattr(self.registry, "commit_activated_enrollment", None)
            if commit is None:
                self._fail_claim(current, "agent_registry_unavailable")
                return
            try:
                commit(current, record, now=self._clock(), cli_accept_receipt=receipt)
            except RegistryConflict:
                existing = self.registry.get(record.agent_id)
                if existing is None or not _same_committed_agent(existing, record):
                    compensate_conflict = True
            except (RegistryError, RevisionConflict):
                return
        if compensate_conflict:
            await self._compensate_activated_add_conflict(current, target)

    async def _compensate_activated_add_conflict(
        self, job: EnrollmentJob, target: Any
    ) -> None:
        current = job
        if current.enrollment_method is not EnrollmentMethod.LEGACY_ADMIN_TOKEN:
            try:
                token = self.credential_store.read(current.credential_temp_ref)
            except CredentialStoreError:
                self._residual_claim(current, "credential_store_unavailable")
                return
            outcome = await self._run_with_recovery_lease(
                current,
                self.agent_client.revoke(
                    target,
                    token,
                    credential_id=current.remote_credential_id,
                ),
            )
            if outcome is None:
                return
            current = outcome.job
            if outcome.error is not None:
                code = (
                    outcome.error.code
                    if isinstance(outcome.error, EnrollmentValidationError)
                    else "agent_network_error"
                )
                dispatch_state = (
                    outcome.error.dispatch_state
                    if isinstance(outcome.error, EnrollmentValidationError)
                    else "unknown"
                )
                self._record_mutation_failure(current, code, dispatch_state)
                self._residual_claim(current, code)
                return
        with self.credential_store.lifecycle_lease():
            try:
                self.credential_store.delete_if_exists(current.credential_temp_ref)
            except CredentialStoreError:
                self._residual_claim(current, "credential_cleanup_failed")
                return
            failed = self._transition_claimed(
                replace(
                    current,
                    credential_temp_ref=None,
                    validated_http_address=None,
                    last_error_code="agent_already_registered",
                ),
                EnrollmentState.FAILED,
                clear_claim=True,
            )
            if failed is not None:
                self._record_mutation_failure(
                    failed, "agent_already_registered", "dispatched"
                )

    async def _recover_rotation(self, job: EnrollmentJob) -> None:
        if (
            job.old_enrollment_method is not EnrollmentMethod.LEGACY_ADMIN_TOKEN
            and job.remote_instance_id != job.old_instance_id
        ):
            self._residual_claim(job, "agent_identity_changed")
            return
        required = (
            job.replace_agent_id,
            job.credential_temp_ref,
            job.old_credential_ref,
            job.old_registry_revision,
            job.old_enrollment_method,
            job.old_display_name,
            job.validated_http_address,
        )
        if self.agent_client is None or any(value is None for value in required):
            self._residual_claim(job, "enrollment_unavailable")
            return
        try:
            target = self.agent_client.prepare_pinned(
                job.normalized_endpoint,
                job.transport_profile_id,
                job.validated_http_address,
            )
            token = self.credential_store.read(job.credential_temp_ref)
        except EnrollmentValidationError as exc:
            self._residual_claim(job, exc.code)
            return
        except CredentialStoreError:
            self._residual_claim(job, "credential_store_unavailable")
            return
        current = job
        if current.state is EnrollmentState.ACTIVATION_REQUESTED:
            outcome = await self._run_with_recovery_lease(
                current,
                self.agent_client.activate(
                    target,
                    token,
                    enrollment_id=current.enrollment_id,
                    credential_id=current.remote_credential_id,
                ),
            )
            if outcome is None:
                return
            current = outcome.job
            if outcome.error is not None:
                code = (
                    outcome.error.code
                    if isinstance(outcome.error, EnrollmentValidationError)
                    else "agent_network_error"
                )
                dispatch_state = (
                    outcome.error.dispatch_state
                    if isinstance(outcome.error, EnrollmentValidationError)
                    else "unknown"
                )
                self._record_mutation_failure(current, code, dispatch_state)
                self._residual_claim(current, code)
                return
            transitioned = self._transition_claimed(
                current, EnrollmentState.ACTIVATED
            )
            if transitioned is None:
                return
            current = transitioned
        registered = self.registry.get(current.replace_agent_id or "")
        if registered is None:
            self._residual_claim(current, "agent_not_found")
            return
        if (
            registered.revision == current.old_registry_revision
            and registered.credential_ref == current.old_credential_ref
        ):
            try:
                registered = self.registry.swap_rotation(current, now=self._clock())
            except (RegistryConflict, RevisionConflict):
                await self._compensate_rotation_registry_mismatch(
                    current, target, token
                )
                return
            except RegistryError:
                self._residual_claim(current, "agent_registry_unavailable")
                return
        elif registered.credential_ref != current.credential_temp_ref:
            await self._compensate_rotation_registry_mismatch(current, target, token)
            return
        renewed = self._renew_claim(current)
        if renewed is None:
            return
        current = renewed
        if current.old_enrollment_method is not EnrollmentMethod.LEGACY_ADMIN_TOKEN:
            outcome = await self._run_with_recovery_lease(
                current,
                self.agent_client.revoke(
                    target,
                    token,
                    credential_id=current.old_remote_credential_id,
                ),
            )
            if outcome is None:
                return
            current = outcome.job
            if outcome.error is not None:
                code = (
                    outcome.error.code
                    if isinstance(outcome.error, EnrollmentValidationError)
                    else "agent_network_error"
                )
                dispatch_state = (
                    outcome.error.dispatch_state
                    if isinstance(outcome.error, EnrollmentValidationError)
                    else "unknown"
                )
                self._record_mutation_failure(current, code, dispatch_state)
                self._residual_claim(current, code)
                return
        with self.credential_store.lifecycle_lease():
            try:
                self.credential_store.delete_if_exists(current.old_credential_ref)
            except CredentialStoreError:
                self._residual_claim(current, "credential_cleanup_failed")
                return
            self._transition_claimed(
                replace(
                    current,
                    credential_temp_ref=None,
                    old_credential_ref=None,
                    old_remote_credential_id=None,
                    validated_http_address=None,
                ),
                EnrollmentState.CONSUMED,
                clear_claim=True,
            )

    async def _compensate_rotation_registry_mismatch(
        self, job: EnrollmentJob, target: Any, token: bytes
    ) -> None:
        """Revoke only the newly activated credential after a lost Registry CAS."""
        marked = self._mark_claim_error(job, "agent_changed")
        if marked is None:
            return
        job = marked
        outcome = await self._run_with_recovery_lease(
            job,
            self.agent_client.revoke(
                target,
                token,
                credential_id=job.remote_credential_id,
            ),
        )
        if outcome is None:
            return
        current = outcome.job
        if outcome.error is not None:
            code = (
                outcome.error.code
                if isinstance(outcome.error, EnrollmentValidationError)
                else "agent_network_error"
            )
            dispatch_state = (
                outcome.error.dispatch_state
                if isinstance(outcome.error, EnrollmentValidationError)
                else "unknown"
            )
            self._record_mutation_failure(current, code, dispatch_state)
            self._residual_claim(current, "agent_changed")
            return

        new_reference = current.credential_temp_ref
        failed = self._transition_claimed(
            replace(
                current,
                credential_temp_ref=None,
                old_credential_ref=None,
                old_remote_credential_id=None,
                validated_http_address=None,
                last_error_code="agent_changed",
            ),
            EnrollmentState.FAILED,
            clear_claim=True,
        )
        if failed is None:
            return
        self._record_mutation_failure(failed, "agent_changed", "dispatched")
        with self.credential_store.lifecycle_lease():
            try:
                self.credential_store.delete_if_exists(new_reference)
            except CredentialStoreError:
                # The terminal orphan sweep can retry this without retaining a live
                # credential in the rotation job or touching either Registry ref.
                return

    def _cleanup_terminal(self, job: EnrollmentJob) -> EnrollmentJob:
        reference = job.credential_temp_ref
        if reference is None:
            return job
        with self.credential_store.lifecycle_lease():
            try:
                self.credential_store.delete_if_exists(reference)
            except CredentialStoreError:
                return self.journal.mark_terminal_cleanup_failed(
                    job.enrollment_id,
                    state=job.state,
                    expected_reference=reference,
                    now=self._clock(),
                )
            return self.journal.finish_terminal_cleanup(
                job.enrollment_id,
                state=job.state,
                expected_reference=reference,
                now=self._clock(),
            )

    async def recover_and_cleanup(self) -> None:
        await self.recover_removals()
        self._recover_auto_startup()
        await self.recover()
        self.credential_store.cleanup_orphans(self.registry, self.journal)

    def _recover_auto_startup(self) -> None:
        for job in self.journal.list_non_terminal():
            if job.enrollment_method not in {
                EnrollmentMethod.SSH_AUTO,
                EnrollmentMethod.SSH_CLI,
                EnrollmentMethod.SSH_SERVICE_KEY,
            }:
                continue
            if (
                job.enrollment_method
                in {EnrollmentMethod.SSH_AUTO, EnrollmentMethod.SSH_SERVICE_KEY}
                and job.state is EnrollmentState.PENDING
            ):
                adapter = (
                    self.service_key_adapter
                    if job.enrollment_method is EnrollmentMethod.SSH_SERVICE_KEY
                    else self.ssh_adapter
                )
                if adapter is None or not adapter.healthy:
                    self.journal.replace_if_state(
                        replace(
                            job,
                            state=EnrollmentState.AWAITING_CLI,
                            enrollment_method=EnrollmentMethod.SSH_CLI,
                            last_error_code="ssh_unavailable",
                            updated_at=self._clock(),
                        ),
                        expected_state=EnrollmentState.PENDING,
                    )
                    continue
                running = self.journal.claim_pending_auto(
                    job.enrollment_id, now=self._clock()
                )
                if running is None:
                    continue
                self._schedule_auto(
                    running,
                    AutoEnrollmentAuditContext(
                        actor_id="system:startup",
                        source_addr=None,
                        correlation_id=None,
                    ),
                )
            elif job.enrollment_method is EnrollmentMethod.SSH_CLI:
                continue
            elif (
                job.state is EnrollmentState.RUNNING
                and job.credential_temp_ref is None
                and job.enrollment_id not in self._background_tasks
            ):
                self.journal.replace_if_state(
                    replace(
                        job,
                        state=EnrollmentState.AWAITING_CLI,
                        enrollment_method=EnrollmentMethod.SSH_CLI,
                        last_error_code="ssh_interaction_required",
                        recovery_owner=None,
                        recovery_lease_until=None,
                        recovery_revision=job.recovery_revision + 1,
                        updated_at=self._clock(),
                    ),
                    expected_state=EnrollmentState.RUNNING,
                )


def _same_committed_agent(existing: AgentRecord, expected: AgentRecord) -> bool:
    return (
        existing.agent_id == expected.agent_id
        and existing.instance_id == expected.instance_id
        and existing.display_name == expected.display_name
        and existing.normalized_endpoint == expected.normalized_endpoint
        and existing.credential_ref == expected.credential_ref
        and existing.remote_credential_id == expected.remote_credential_id
        and existing.transport_profile_id == expected.transport_profile_id
        and existing.enrollment_method is expected.enrollment_method
        and existing.enabled == expected.enabled
        and existing.source == expected.source
    )


def _cli_accept_receipt(
    *,
    enrollment_id: str,
    nonce: str,
    peer_uid: int,
    input_fingerprint: str,
    pinned_address: str,
) -> str:
    digest = hashlib.sha256()
    digest.update(b"ic-env-guard.cli-accept-receipt.v1\x00")
    for value in (
        enrollment_id.encode("utf-8"),
        nonce.encode("ascii"),
        str(peer_uid).encode("ascii"),
        input_fingerprint.encode("ascii"),
        pinned_address.encode("ascii"),
    ):
        digest.update(len(value).to_bytes(4, "big"))
        digest.update(value)
    return digest.hexdigest()
