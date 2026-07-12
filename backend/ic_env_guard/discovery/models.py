from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DiscoveryState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class DiscoveryDispatchState(str, Enum):
    NOT_DISPATCHED = "not_dispatched"
    UNKNOWN = "unknown"
    DISPATCHED = "dispatched"


@dataclass(frozen=True)
class DiscoveryTarget:
    ip: str
    port: int
    transport_profile_id: str
    scheme: str

    @property
    def canonical_url(self) -> str:
        host = f"[{self.ip}]" if ":" in self.ip else self.ip
        return f"{self.scheme}://{host}:{self.port}"


@dataclass(frozen=True)
class DiscoveryFingerprint:
    version: str


@dataclass(frozen=True)
class DiscoveryJob:
    job_id: str
    scope_id: str
    state: DiscoveryState
    total_targets: int
    checked_targets: int
    found_targets: int
    cancel_requested: bool
    safe_error_code: str | None
    start_audit_event_id: int
    deadline_at: datetime
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    aggregate_dispatch_state: DiscoveryDispatchState = (
        DiscoveryDispatchState.NOT_DISPATCHED
    )


@dataclass(frozen=True)
class DiscoveryResult:
    result_id: str
    job_id: str
    canonical_url: str
    ip: str
    port: int
    transport_profile_id: str
    fingerprint_version: str | None
    found: bool
    safe_error_code: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    linked_enrollment_id: str | None = None
