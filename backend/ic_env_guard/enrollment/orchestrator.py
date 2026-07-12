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
from ic_env_guard.fleet.models import (
    AgentRecord,
    EnrollmentJob,
    EnrollmentMethod,
    EnrollmentState,
    RegistryConflict,
)
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


class EnrollmentOrchestrator:
    def __init__(
        self,
        *,
        jobs: EnrollmentJobs,
        journal: EnrollmentJournalRepository,
        credential_store: CredentialStore,
        agent_client: EnrollmentAgentClient | None,
        registry: Any,
    ) -> None:
        self.jobs = jobs
        self.journal = journal
        self.credential_store = credential_store
        self.agent_client = agent_client
        self.registry = registry
        self._validation_cache: dict[str, EnrollmentValidation] = {}
        self._recovery_owner = str(uuid4())
        self._recovery_lease_seconds = 30

    def create(self, request: EnrollmentJobRequest) -> EnrollmentPublicResult:
        return EnrollmentPublicResult(self.jobs.create(request))

    def get(self, enrollment_id: str) -> EnrollmentPublicResult:
        return EnrollmentPublicResult(
            self.jobs.get(enrollment_id), self._validation_cache.get(enrollment_id)
        )

    def cancel(self, enrollment_id: str) -> EnrollmentPublicResult:
        return EnrollmentPublicResult(self._cleanup_terminal(self.jobs.cancel(enrollment_id)))

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
                            last_error_code="agent_validation_failed",
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
        claimed = self.journal.expire_and_claim_recovery(
            owner=self._recovery_owner,
            now=datetime.now(UTC),
            lease_seconds=self._recovery_lease_seconds,
        )
        for job in claimed:
            if job.state in {
                EnrollmentState.ACTIVATION_REQUESTED,
                EnrollmentState.ACTIVATED,
            } and job.save_requested:
                try:
                    await self._recover_activation(job)
                except EnrollmentValidationError as exc:
                    self.journal.fail_recovery_claim(
                        job.enrollment_id,
                        owner=self._recovery_owner,
                        error_code=exc.code,
                        now=datetime.now(UTC),
                    )
                except CredentialStoreError:
                    self.journal.fail_recovery_claim(
                        job.enrollment_id,
                        owner=self._recovery_owner,
                        error_code="credential_store_unavailable",
                        now=datetime.now(UTC),
                    )
                continue
            if job.state not in {
                EnrollmentState.CREDENTIAL_ISSUED,
                EnrollmentState.VERIFYING,
                EnrollmentState.VERIFIED,
            }:
                continue
            if self.agent_client is None:
                self.journal.fail_recovery_claim(
                    job.enrollment_id,
                    owner=self._recovery_owner,
                    error_code="enrollment_unavailable",
                    now=datetime.now(UTC),
                )
                continue
            if not job.credential_temp_ref:
                self.journal.fail_recovery_claim(
                    job.enrollment_id,
                    owner=self._recovery_owner,
                    error_code="credential_store_unavailable",
                    now=datetime.now(UTC),
                )
                continue
            current = job
            if current.state is EnrollmentState.CREDENTIAL_ISSUED:
                current = self.journal.replace_if_state(
                    replace(current, state=EnrollmentState.VERIFYING, updated_at=datetime.now(UTC)),
                    expected_state=EnrollmentState.CREDENTIAL_ISSUED,
                    expected_recovery_owner=self._recovery_owner,
                )
            try:
                target = self.agent_client.prepare(
                    current.normalized_endpoint, current.transport_profile_id
                )
                token = self.credential_store.read(current.credential_temp_ref)
                if current.enrollment_method is EnrollmentMethod.LEGACY_ADMIN_TOKEN:
                    validation = await self.agent_client.validate_legacy(target, token)
                else:
                    if not current.remote_instance_id:
                        self.journal.fail_recovery_claim(
                            current.enrollment_id,
                            owner=self._recovery_owner,
                            error_code="agent_identity_missing",
                            now=datetime.now(UTC),
                        )
                        continue
                    validation = await self.agent_client.validate_pending(
                        target, token, helper_instance_id=current.remote_instance_id
                    )
            except EnrollmentValidationError as exc:
                self.journal.fail_recovery_claim(
                    current.enrollment_id,
                    owner=self._recovery_owner,
                    error_code=exc.code,
                    now=datetime.now(UTC),
                )
                continue
            except CredentialStoreError:
                self.journal.fail_recovery_claim(
                    current.enrollment_id,
                    owner=self._recovery_owner,
                    error_code="credential_store_unavailable",
                    now=datetime.now(UTC),
                )
                continue
            if current.state is EnrollmentState.VERIFIED:
                self._validation_cache[current.enrollment_id] = validation
                self.journal.release_recovery_claim(
                    current.enrollment_id, owner=self._recovery_owner
                )
                continue
            verified = self.journal.replace_if_state(
                replace(
                    current,
                    state=EnrollmentState.VERIFIED,
                    updated_at=datetime.now(UTC),
                    recovery_owner=None,
                    recovery_lease_until=None,
                ),
                expected_state=EnrollmentState.VERIFYING,
                expected_recovery_owner=self._recovery_owner,
            )
            self._validation_cache[verified.enrollment_id] = validation
        for terminal in self.journal.list_terminal_cleanup():
            self._cleanup_terminal(terminal)

    async def _recover_activation(self, job: EnrollmentJob) -> None:
        if not job.credential_temp_ref or not job.requested_display_name:
            return
        current = job
        if current.state is EnrollmentState.ACTIVATION_REQUESTED:
            if current.enrollment_method is not EnrollmentMethod.LEGACY_ADMIN_TOKEN:
                if self.agent_client is None or not current.remote_credential_id:
                    raise EnrollmentValidationError(
                        "enrollment_unavailable", dispatch_state="not_dispatched"
                    )
                target = self.agent_client.prepare(
                    current.normalized_endpoint, current.transport_profile_id
                )
                await self.agent_client.activate(
                    target,
                    self.credential_store.read(current.credential_temp_ref),
                    enrollment_id=current.enrollment_id,
                    credential_id=current.remote_credential_id,
                )
            current = self.journal.replace_if_state(
                replace(current, state=EnrollmentState.ACTIVATED, updated_at=datetime.now(UTC)),
                expected_state=EnrollmentState.ACTIVATION_REQUESTED,
                expected_recovery_owner=self._recovery_owner,
            )
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
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        with self.credential_store.lifecycle_lease():
            try:
                self.registry.create(record)
            except RegistryConflict:
                existing = self.registry.get(record.agent_id)
                if existing is None or not _same_committed_agent(existing, record):
                    return
            self.journal.replace_if_state(
                replace(
                    current,
                    state=EnrollmentState.CONSUMED,
                    credential_temp_ref=None,
                    updated_at=datetime.now(UTC),
                    recovery_owner=None,
                    recovery_lease_until=None,
                ),
                expected_state=EnrollmentState.ACTIVATED,
                expected_recovery_owner=self._recovery_owner,
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
        await self.recover()
        self.credential_store.cleanup_orphans(self.registry, self.journal)


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
