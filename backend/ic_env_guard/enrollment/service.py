import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ic_env_guard.enrollment.models import (
    CredentialNotFound,
    CredentialState,
    EnrollmentForbidden,
    IssuedCredential,
    ManagerCredential,
    ManagerCredentialContext,
    aware_utc,
    canonical_uuid,
    valid_enrollment_id,
)
from ic_env_guard.enrollment.ports import EnrollmentAudit, ManagerCredentialRepository
from ic_env_guard.enrollment.protocol import PENDING_CREDENTIAL_TTL_SECONDS


class EnrollmentService:
    def __init__(
        self,
        repository: ManagerCredentialRepository,
        audit: EnrollmentAudit,
        *,
        pending_ttl_seconds: int = PENDING_CREDENTIAL_TTL_SECONDS,
        max_pending: int = 64,
    ) -> None:
        self.repository = repository
        self.audit = audit
        self.pending_ttl_seconds = pending_ttl_seconds
        self.max_pending = max_pending

    def issue_pending(
        self, manager_id: str, enrollment_id: str, *, now: datetime | None = None
    ) -> IssuedCredential:
        current = aware_utc(now or datetime.now(UTC), "now")
        manager_id = canonical_uuid(manager_id, "manager_id")
        enrollment_id = valid_enrollment_id(enrollment_id)
        operation = "credential.issue"
        self.audit.record_intent(
            operation=operation, actor_id="local-admin", credential_id=None
        )
        token = secrets.token_urlsafe(32)
        record = ManagerCredential(
            credential_id=str(uuid4()),
            manager_id=manager_id,
            enrollment_id=enrollment_id,
            token_hash=_token_hash(token),
            state=CredentialState.PENDING,
            pending_expires_at=current + timedelta(seconds=self.pending_ttl_seconds),
            created_at=current,
        )
        try:
            self.repository.issue(record, now=current, max_pending=self.max_pending)
        except Exception:
            self._record_outcome(
                operation=operation,
                actor_id="local-admin",
                credential_id=None,
                result="failed",
            )
            raise
        self._record_outcome(
            operation=operation,
            actor_id="local-admin",
            credential_id=record.credential_id,
            result="success",
        )
        return IssuedCredential(record, token)

    def verify(
        self, token: str, *, now: datetime | None = None
    ) -> ManagerCredentialContext | None:
        return self.authenticate(token, now)

    def authenticate(
        self, token: str, now: datetime | None = None
    ) -> ManagerCredentialContext | None:
        current = aware_utc(now or datetime.now(UTC), "now")
        candidate = _token_hash(token)
        matched: ManagerCredential | None = None
        for record in self.repository.authenticatable():
            if hmac.compare_digest(candidate, record.token_hash):
                matched = record
        if matched is None:
            return None
        if matched.state is CredentialState.PENDING and (
            matched.pending_expires_at is None or current >= matched.pending_expires_at
        ):
            return None
        return ManagerCredentialContext(
            credential_id=matched.credential_id,
            manager_id=matched.manager_id,
            state=matched.state,
        )

    def authenticate_revoked_for_revoke(
        self, token: str, credential_id: str
    ) -> ManagerCredentialContext | None:
        try:
            target_id = canonical_uuid(credential_id, "credential_id")
        except ValueError:
            return None
        candidate = _token_hash(token)
        matched: ManagerCredential | None = None
        for record in self.repository.list_all():
            if hmac.compare_digest(candidate, record.token_hash):
                matched = record
        if (
            matched is None
            or matched.state is not CredentialState.REVOKED
            or matched.credential_id != target_id
        ):
            return None
        return ManagerCredentialContext(
            credential_id=matched.credential_id,
            manager_id=matched.manager_id,
            state=matched.state,
        )

    def activate(
        self,
        credential_id: str,
        enrollment_id: str,
        token: str,
        *,
        now: datetime | None = None,
    ) -> ManagerCredential:
        current = aware_utc(now or datetime.now(UTC), "now")
        credential_id = canonical_uuid(credential_id, "credential_id")
        enrollment_id = valid_enrollment_id(enrollment_id)
        context = self.authenticate(token, current)
        actor_id = context.actor_id if context is not None else "unknown"
        operation = "credential.activate"
        self.audit.record_intent(
            operation=operation, actor_id=actor_id, credential_id=credential_id
        )
        if (
            context is None
            or context.credential_id != credential_id
            or context.state not in (CredentialState.PENDING, CredentialState.ACTIVE)
        ):
            self._record_outcome(
                operation=operation,
                actor_id=actor_id,
                credential_id=credential_id,
                result="denied",
            )
            raise EnrollmentForbidden("credential activation is forbidden")
        record = self.repository.activate(
            credential_id, enrollment_id, _token_hash(token), current
        )
        if record is None:
            self._record_outcome(
                operation=operation,
                actor_id=actor_id,
                credential_id=credential_id,
                result="denied",
            )
            raise EnrollmentForbidden("credential activation is forbidden")
        self._record_outcome(
            operation=operation,
            actor_id=record.actor_id,
            credential_id=credential_id,
            result="success",
        )
        return record

    def revoke(
        self,
        credential_id: str,
        *,
        actor_id: str,
        manager_id: str | None,
        now: datetime | None = None,
    ) -> ManagerCredential:
        current = aware_utc(now or datetime.now(UTC), "now")
        credential_id = canonical_uuid(credential_id, "credential_id")
        if manager_id is not None:
            manager_id = canonical_uuid(manager_id, "manager_id")
        operation = "credential.revoke"
        self.audit.record_intent(
            operation=operation, actor_id=actor_id, credential_id=credential_id
        )
        record = self.repository.get(credential_id)
        if record is None:
            self._record_outcome(
                operation=operation,
                actor_id=actor_id,
                credential_id=credential_id,
                result="failed",
            )
            raise CredentialNotFound("credential not found")
        if actor_id != "local-admin" and (
            manager_id is None or manager_id != record.manager_id
        ):
            self._record_outcome(
                operation=operation,
                actor_id=actor_id,
                credential_id=credential_id,
                result="denied",
            )
            raise EnrollmentForbidden("credential revocation is forbidden")
        revoked = self.repository.revoke(credential_id, current)
        if revoked is None:
            self._record_outcome(
                operation=operation,
                actor_id=actor_id,
                credential_id=credential_id,
                result="failed",
            )
            raise CredentialNotFound("credential not found")
        self._record_outcome(
            operation=operation,
            actor_id=actor_id,
            credential_id=credential_id,
            result="success",
        )
        return revoked

    def list(self, *, actor_id: str) -> tuple[ManagerCredential, ...]:
        if actor_id != "local-admin":
            raise EnrollmentForbidden("credential listing is forbidden")
        return self.repository.list_all()

    def _record_outcome(
        self, *, operation: str, actor_id: str, credential_id: str | None, result: str
    ) -> None:
        try:
            self.audit.record_outcome(
                operation=operation,
                actor_id=actor_id,
                credential_id=credential_id,
                result=result,
            )
        except Exception:
            # The mutation may already be durable; never invite an unsafe retry.
            return


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
