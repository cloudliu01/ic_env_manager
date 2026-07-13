from datetime import datetime
from typing import Protocol

from ic_env_guard.enrollment.models import ManagerCredential, ManagerCredentialContext


class ManagerCredentialRepository(Protocol):
    def issue(self, record: ManagerCredential, *, now: datetime, max_pending: int) -> None: ...

    def reissue_expired(
        self, record: ManagerCredential, *, now: datetime, max_pending: int
    ) -> None: ...

    def get(self, credential_id: str) -> ManagerCredential | None: ...

    def list_all(self) -> tuple[ManagerCredential, ...]: ...

    def authenticatable(self) -> tuple[ManagerCredential, ...]: ...

    def activate(
        self, credential_id: str, enrollment_id: str, token_hash: str, now: datetime
    ) -> ManagerCredential | None: ...

    def revoke(self, credential_id: str, now: datetime) -> ManagerCredential | None: ...

class ManagerCredentialVerifier(Protocol):
    def authenticate(
        self, token: str, now: datetime | None = None
    ) -> ManagerCredentialContext | None: ...


class EnrollmentAudit(Protocol):
    def record_intent(
        self, *, operation: str, actor_id: str, credential_id: str | None
    ) -> None: ...

    def record_outcome(
        self, *, operation: str, actor_id: str, credential_id: str | None, result: str
    ) -> None: ...
