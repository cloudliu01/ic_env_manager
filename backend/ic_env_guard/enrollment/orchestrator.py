import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ic_env_guard.enrollment.agent_client import (
    EnrollmentAgentClient,
    EnrollmentValidation,
    EnrollmentValidationError,
)
from ic_env_guard.enrollment.credential_store import CredentialStore, CredentialStoreError
from ic_env_guard.enrollment.jobs import EnrollmentJobRequest, EnrollmentJobs
from ic_env_guard.enrollment.ssh import (
    SshEnrollmentAdapter,
    SshEnrollmentError,
    SshEnrollmentRequest,
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
from ic_env_guard.fleet.transport import TransportProfile
from ic_env_guard.storage.enrollment_journal import EnrollmentJournalRepository

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
        transport_profiles: tuple[TransportProfile, ...] = (),
        auto_audit: Any | None = None,
    ) -> None:
        self.jobs = jobs
        self.journal = journal
        self.credential_store = credential_store
        self.agent_client = agent_client
        self.registry = registry
        self._now = clock or (lambda: datetime.now(UTC))
        self.ssh_adapter = ssh_adapter
        self._transport_profiles = {
            profile.id: profile for profile in transport_profiles
        }
        self._auto_audit = auto_audit
        self._background_tasks: dict[str, asyncio.Task[None]] = {}
        self._closing = False
        self._validation_cache: dict[str, EnrollmentValidation] = {}
        self._recovery_owner = str(uuid4())
        max_operation = getattr(agent_client, "max_network_operation_seconds", 10.0)
        self._recovery_lease_seconds = max(3, math.ceil(float(max_operation) * 2))

    def create(self, request: EnrollmentJobRequest) -> EnrollmentPublicResult:
        return EnrollmentPublicResult(self.jobs.create(request))

    def create_auto(
        self,
        request: EnrollmentJobRequest,
        audit_context: AutoEnrollmentAuditContext,
    ) -> EnrollmentPublicResult:
        request = replace(request, enrollment_method=EnrollmentMethod.SSH_AUTO)
        pending = self.jobs.create(request, now=self._now())
        adapter = self.ssh_adapter
        if self._closing or adapter is None or not adapter.healthy:
            awaiting = self.journal.replace_if_state(
                replace(
                    pending,
                    state=EnrollmentState.AWAITING_CLI,
                    last_error_code="ssh_unavailable",
                    updated_at=self._now(),
                ),
                expected_state=EnrollmentState.PENDING,
            )
            return EnrollmentPublicResult(awaiting)
        running = self.journal.claim_pending_auto(
            pending.enrollment_id, now=self._now()
        )
        if running is None:
            current = self.journal.get(pending.enrollment_id)
            if current is None:
                raise RegistryError("enrollment journal storage is unavailable")
            return EnrollmentPublicResult(current)
        self._schedule_auto(running, audit_context)
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
            self.jobs.get(enrollment_id), self._validation_cache.get(enrollment_id)
        )

    async def cancel(self, enrollment_id: str) -> EnrollmentPublicResult:
        task = self._background_tasks.get(enrollment_id)
        if task is not None:
            task.cancel()
        with self.credential_store.lifecycle_lease():
            return EnrollmentPublicResult(
                self._cleanup_terminal(self.jobs.cancel(enrollment_id))
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
        job = self.jobs.create(job_request)
        reference = None
        try:
            with self.credential_store.lifecycle_lease():
                reference = self.credential_store.put(token.encode("utf-8"))
                issued = replace(
                    job,
                    state=EnrollmentState.RUNNING,
                    updated_at=datetime.now(UTC),
                )
                issued = self.journal.replace_if_state(
                    issued, expected_state=EnrollmentState.PENDING
                )
                issued = self.journal.replace_if_state(
                    replace(
                        issued,
                        state=EnrollmentState.CREDENTIAL_ISSUED,
                        credential_temp_ref=reference,
                        updated_at=datetime.now(UTC),
                    ),
                    expected_state=EnrollmentState.RUNNING,
                )
            verifying = self.journal.replace_if_state(
                replace(issued, state=EnrollmentState.VERIFYING, updated_at=datetime.now(UTC)),
                expected_state=EnrollmentState.CREDENTIAL_ISSUED,
            )
            validation = await self.agent_client.validate_legacy(
                target, self.credential_store.read(reference)
            )
            verified = self.journal.replace_if_state(
                replace(verifying, state=EnrollmentState.VERIFIED, updated_at=datetime.now(UTC)),
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
                            updated_at=datetime.now(UTC),
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
        for enrollment_id in self.journal.prepare_recovery(now=self._now()):
            job = self.journal.claim_recovery(
                enrollment_id,
                owner=self._recovery_owner,
                now=self._now(),
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
                job.enrollment_id, now=self._now()
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
        adapter = self.ssh_adapter
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
                        updated_at=self._now(),
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
                updated_at=self._now(),
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
                updated_at=self._now(),
            ),
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
                    updated_at=self._now(),
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
                now=self._now(),
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
                now=self._now(),
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
        now = self._now()
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
                now=self._now(),
            )
        except RegistryError:
            return

    def _reload_after_fence_loss(self, enrollment_id: str) -> None:
        try:
            self.journal.get(enrollment_id)
        except RegistryError:
            return

    async def _recover_activation(self, job: EnrollmentJob) -> None:
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
            created_at=self._now(),
            updated_at=self._now(),
        )
        with self.credential_store.lifecycle_lease():
            try:
                self.registry.create(record)
            except RegistryConflict:
                existing = self.registry.get(record.agent_id)
                if existing is None or not _same_committed_agent(existing, record):
                    return
            self._transition_claimed(
                replace(
                    current,
                    credential_temp_ref=None,
                    validated_http_address=None,
                ),
                EnrollmentState.CONSUMED,
                clear_claim=True,
            )

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
                    now=datetime.now(UTC),
                )
            return self.journal.finish_terminal_cleanup(
                job.enrollment_id,
                state=job.state,
                expected_reference=reference,
                now=datetime.now(UTC),
            )

    async def recover_and_cleanup(self) -> None:
        self._recover_auto_startup()
        await self.recover()
        self.credential_store.cleanup_orphans(self.registry, self.journal)

    def _recover_auto_startup(self) -> None:
        for job in self.journal.list_non_terminal():
            if job.enrollment_method is not EnrollmentMethod.SSH_AUTO:
                continue
            if job.state is EnrollmentState.PENDING:
                if self.ssh_adapter is None or not self.ssh_adapter.healthy:
                    self.journal.replace_if_state(
                        replace(
                            job,
                            state=EnrollmentState.AWAITING_CLI,
                            last_error_code="ssh_unavailable",
                            updated_at=self._now(),
                        ),
                        expected_state=EnrollmentState.PENDING,
                    )
                    continue
                running = self.journal.claim_pending_auto(
                    job.enrollment_id, now=self._now()
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
            elif (
                job.state is EnrollmentState.RUNNING
                and job.credential_temp_ref is None
                and job.enrollment_id not in self._background_tasks
            ):
                self.journal.replace_if_state(
                    replace(
                        job,
                        state=EnrollmentState.AWAITING_CLI,
                        last_error_code="ssh_interaction_required",
                        updated_at=self._now(),
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
