import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ic_env_guard.fleet.models import (
    EnrollmentJob,
    EnrollmentMethod,
    EnrollmentState,
    RegistryConflict,
    RevisionConflict,
)
from ic_env_guard.storage.enrollment_journal import EnrollmentJournalRepository


class EnrollmentConflict(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


EXPIRABLE_STATES = {
    EnrollmentState.PENDING,
    EnrollmentState.RUNNING,
    EnrollmentState.AWAITING_CLI,
    EnrollmentState.CREDENTIAL_ISSUED,
    EnrollmentState.VERIFYING,
    EnrollmentState.VERIFIED,
}


@dataclass(frozen=True)
class EnrollmentJobRequest:
    normalized_endpoint: str
    transport_profile_id: str
    display_name: str | None = None
    ssh_user: str | None = None
    ssh_host: str | None = None
    ssh_port: int | None = None
    enrollment_method: EnrollmentMethod = EnrollmentMethod.SSH_CLI
    discovery_result_id: str | None = None
    replace_agent_id: str | None = None


def enrollment_input_fingerprint(request: EnrollmentJobRequest) -> str:
    payload = {
        "endpoint": request.normalized_endpoint,
        "profile": request.transport_profile_id,
        "ssh_host": request.ssh_host,
        "ssh_port": request.ssh_port,
        "ssh_user": request.ssh_user,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def job_input_fingerprint(job: EnrollmentJob) -> str:
    return enrollment_input_fingerprint(
        EnrollmentJobRequest(
            normalized_endpoint=job.normalized_endpoint,
            transport_profile_id=job.transport_profile_id,
            ssh_user=job.ssh_user,
            ssh_host=job.ssh_host,
            ssh_port=job.ssh_port,
            enrollment_method=job.enrollment_method,
        )
    )


class EnrollmentJobs:
    def __init__(
        self,
        repository: EnrollmentJournalRepository,
        *,
        manager_id: str,
        pending_ttl_seconds: int,
        max_active: int,
        discovery_retention_seconds: int = 86_400,
    ) -> None:
        self.repository = repository
        self.manager_id = manager_id
        self.pending_ttl_seconds = pending_ttl_seconds
        self.max_active = max_active
        self.discovery_retention_seconds = discovery_retention_seconds

    def create(
        self,
        request: EnrollmentJobRequest,
        *,
        enrollment_id: str | None = None,
        now: datetime | None = None,
        state: EnrollmentState = EnrollmentState.PENDING,
    ) -> EnrollmentJob:
        now = now or datetime.now(UTC)
        job = EnrollmentJob(
            enrollment_id=enrollment_id if enrollment_id is not None else str(uuid4()),
            manager_id=self.manager_id,
            state=state,
            normalized_endpoint=request.normalized_endpoint,
            transport_profile_id=request.transport_profile_id,
            discovery_result_id=request.discovery_result_id,
            replace_agent_id=request.replace_agent_id,
            requested_display_name=request.display_name,
            ssh_user=request.ssh_user,
            ssh_host=request.ssh_host,
            ssh_port=request.ssh_port,
            enrollment_method=request.enrollment_method,
            remote_instance_id=None,
            remote_credential_id=None,
            credential_temp_ref=None,
            old_credential_ref=None,
            old_remote_credential_id=None,
            save_requested=False,
            expires_at=now + timedelta(seconds=self.pending_ttl_seconds),
            last_error_code=None,
            created_at=now,
            updated_at=now,
        )
        try:
            if request.discovery_result_id is not None:
                return self.repository.create_discovery_with_capacity(
                    job,
                    now=now,
                    max_active=self.max_active,
                    retention_seconds=self.discovery_retention_seconds,
                )
            return self.repository.create_with_capacity(job, now=now, max_active=self.max_active)
        except RegistryConflict as exc:
            code = str(exc)
            if code in {
                "agent_enrollment_capacity",
                "agent_validation_changed",
                "transport_profile_mismatch",
            }:
                raise EnrollmentConflict(code) from exc
            raise EnrollmentConflict("agent_enrollment_conflict") from exc

    def create_rotation(
        self,
        request: EnrollmentJobRequest,
        *,
        now: datetime | None = None,
    ) -> EnrollmentJob:
        now = now or datetime.now(UTC)
        if request.replace_agent_id is None:
            raise EnrollmentConflict("agent_not_found")
        job = EnrollmentJob(
            enrollment_id=str(uuid4()),
            manager_id=self.manager_id,
            state=EnrollmentState.PENDING,
            normalized_endpoint=request.normalized_endpoint,
            transport_profile_id=request.transport_profile_id,
            discovery_result_id=None,
            replace_agent_id=request.replace_agent_id,
            requested_display_name=request.display_name,
            ssh_user=request.ssh_user,
            ssh_host=request.ssh_host,
            ssh_port=request.ssh_port,
            enrollment_method=request.enrollment_method,
            remote_instance_id=None,
            remote_credential_id=None,
            credential_temp_ref=None,
            old_credential_ref=None,
            old_remote_credential_id=None,
            save_requested=False,
            expires_at=now + timedelta(seconds=self.pending_ttl_seconds),
            last_error_code=None,
            created_at=now,
            updated_at=now,
        )
        try:
            return self.repository.create_rotation_with_capacity(
                job, now=now, max_active=self.max_active
            )
        except RegistryConflict as exc:
            code = str(exc)
            if code in {
                "agent_not_found",
                "agent_enrollment_capacity",
                "agent_mutation_in_progress",
            }:
                raise EnrollmentConflict(code) from exc
            raise EnrollmentConflict("agent_enrollment_conflict") from exc

    def get(self, enrollment_id: str, *, now: datetime | None = None) -> EnrollmentJob:
        job = self.repository.get(enrollment_id)
        if job is None:
            raise EnrollmentConflict("agent_enrollment_not_found")
        now = now or datetime.now(UTC)
        if job.state in EXPIRABLE_STATES and now >= job.expires_at:
            try:
                job = self.repository.replace_if_state(
                    replace(
                        job,
                        state=EnrollmentState.EXPIRED,
                        recovery_owner=None,
                        recovery_lease_until=None,
                        recovery_revision=job.recovery_revision + 1,
                        cli_resume_nonce=None,
                        cli_peer_uid=None,
                        cli_input_fingerprint=None,
                        cli_pinned_address=None,
                        cli_accept_receipt=None,
                        updated_at=now,
                    ),
                    expected_state=job.state,
                )
            except RevisionConflict:
                job = self.repository.get(enrollment_id) or job
        return job

    def cancel(self, enrollment_id: str, *, now: datetime | None = None) -> EnrollmentJob:
        now = now or datetime.now(UTC)
        job = self.get(enrollment_id, now=now)
        if job.state is EnrollmentState.EXPIRED:
            raise EnrollmentConflict("agent_enrollment_expired")
        if job.state not in {
            EnrollmentState.PENDING,
            EnrollmentState.RUNNING,
            EnrollmentState.AWAITING_CLI,
            EnrollmentState.CREDENTIAL_ISSUED,
            EnrollmentState.VERIFYING,
            EnrollmentState.VERIFIED,
        }:
            raise EnrollmentConflict("agent_enrollment_not_cancellable")
        try:
            return self.repository.replace_if_state(
                replace(
                    job,
                    state=EnrollmentState.CANCELLED,
                    recovery_owner=None,
                    recovery_lease_until=None,
                    recovery_revision=job.recovery_revision + 1,
                    cli_resume_nonce=None,
                    cli_peer_uid=None,
                    cli_input_fingerprint=None,
                    cli_pinned_address=None,
                    cli_accept_receipt=None,
                    updated_at=now,
                ),
                expected_state=job.state,
            )
        except RevisionConflict as exc:
            raise EnrollmentConflict("agent_enrollment_conflict") from exc

    def consume(
        self,
        enrollment_id: str,
        *,
        display_name: str,
        input_fingerprint: str,
        now: datetime | None = None,
    ) -> EnrollmentJob:
        now = now or datetime.now(UTC)
        job = self.get(enrollment_id, now=now)
        if job.state is EnrollmentState.CONSUMED:
            raise EnrollmentConflict("agent_enrollment_consumed")
        if job.state is EnrollmentState.EXPIRED:
            raise EnrollmentConflict("agent_enrollment_expired")
        if job.state is not EnrollmentState.VERIFIED:
            raise EnrollmentConflict("agent_enrollment_not_verified")
        if input_fingerprint != job_input_fingerprint(job):
            raise EnrollmentConflict("agent_enrollment_input_changed")
        try:
            return self.repository.replace_if_state(
                replace(
                    job,
                    state=EnrollmentState.ACTIVATION_REQUESTED,
                    requested_display_name=display_name,
                    save_requested=True,
                    updated_at=now,
                ),
                expected_state=EnrollmentState.VERIFIED,
            )
        except RevisionConflict as exc:
            raise EnrollmentConflict("agent_enrollment_conflict") from exc

    def consume_rotation(
        self,
        enrollment_id: str,
        *,
        agent_id: str,
        display_name: str,
        now: datetime | None = None,
    ) -> EnrollmentJob:
        try:
            return self.repository.consume_rotation(
                enrollment_id,
                agent_id=agent_id,
                display_name=display_name,
                now=now or datetime.now(UTC),
            )
        except RegistryConflict as exc:
            code = str(exc)
            if code in {
                "agent_enrollment_not_found",
                "agent_enrollment_consumed",
                "agent_enrollment_expired",
                "agent_enrollment_not_verified",
                "agent_enrollment_conflict",
                "agent_not_found",
                "agent_changed",
                "agent_mutation_in_progress",
            }:
                raise EnrollmentConflict(code) from exc
            raise EnrollmentConflict("agent_enrollment_conflict") from exc
        except RevisionConflict as exc:
            raise EnrollmentConflict("agent_enrollment_conflict") from exc
