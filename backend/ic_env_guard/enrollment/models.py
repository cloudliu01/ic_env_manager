import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID


class EnrollmentError(Exception):
    pass


class DuplicateEnrollment(EnrollmentError):
    pass


class EnrollmentCapacityExceeded(EnrollmentError):
    pass


class EnrollmentForbidden(EnrollmentError):
    pass


class CredentialNotFound(EnrollmentError):
    pass


class CredentialStorageError(EnrollmentError):
    pass


class CredentialState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"


_ENROLLMENT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def canonical_uuid(value: str, field: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise ValueError(f"{field} must be a canonical lowercase UUID")
    return canonical


def valid_enrollment_id(value: str) -> str:
    if not _ENROLLMENT_ID.fullmatch(value):
        raise ValueError("invalid enrollment_id")
    return value


def aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ManagerCredential:
    credential_id: str
    manager_id: str
    enrollment_id: str
    token_hash: str
    state: CredentialState
    pending_expires_at: datetime | None
    created_at: datetime
    activated_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def actor_id(self) -> str:
        if self.state in (CredentialState.ACTIVE, CredentialState.REVOKED):
            return f"manager:{self.manager_id}"
        return f"pending-manager:{self.manager_id}"

    def safe_dict(self) -> dict[str, str | None]:
        return {
            "credential_id": self.credential_id,
            "manager_id": self.manager_id,
            "state": self.state.value,
            "pending_expires_at": _iso(self.pending_expires_at),
            "created_at": _iso(self.created_at),
            "activated_at": _iso(self.activated_at),
            "last_used_at": _iso(self.last_used_at),
            "revoked_at": _iso(self.revoked_at),
        }


@dataclass(frozen=True)
class IssuedCredential:
    credential: ManagerCredential
    token: str

    @property
    def credential_id(self) -> str:
        return self.credential.credential_id


@dataclass(frozen=True)
class ManagerCredentialContext:
    credential_id: str
    manager_id: str
    state: CredentialState

    @property
    def actor_id(self) -> str:
        if self.state in (CredentialState.ACTIVE, CredentialState.REVOKED):
            return f"manager:{self.manager_id}"
        return f"pending-manager:{self.manager_id}"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
