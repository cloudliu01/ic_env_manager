from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class RegistryError(Exception):
    pass


class RegistryConflict(RegistryError):
    pass


class RevisionConflict(RegistryError):
    pass


class RegistryInvariantError(RegistryError):
    pass


class EnrollmentMethod(str, Enum):
    SSH_AUTO = "ssh_auto"
    SSH_CLI = "ssh_cli"
    SSH_SERVICE_KEY = "ssh_service_key"
    LOCAL_SOCKET = "local_socket"
    LEGACY_ADMIN_TOKEN = "legacy_admin_token"


class EnrollmentState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_CLI = "awaiting_cli"
    CREDENTIAL_ISSUED = "credential_issued"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    ACTIVATION_REQUESTED = "activation_requested"
    ACTIVATED = "activated"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    EXPIRED = "expired"

    @property
    def terminal(self) -> bool:
        return self in {
            EnrollmentState.CONSUMED,
            EnrollmentState.CANCELLED,
            EnrollmentState.FAILED,
            EnrollmentState.EXPIRED,
        }


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    instance_id: str | None
    display_name: str
    normalized_endpoint: str
    credential_ref: str
    remote_credential_id: str | None
    transport_profile_id: str
    enrollment_method: EnrollmentMethod
    enabled: bool
    source: str
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AgentQuery:
    limit: int = 100
    cursor: str | None = None


@dataclass(frozen=True)
class AgentPage:
    items: tuple[AgentRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class AgentStatus:
    agent_id: str
    target_revision: int
    connection_status: str
    workload_status: str
    observed_at: datetime | None
    stale_after: datetime | None
    api_version: str | None
    agent_version: str | None
    capabilities: tuple[str, ...]
    summary: dict[str, Any]
    last_error_code: str | None
    updated_at: datetime


@dataclass(frozen=True)
class EnrollmentJob:
    enrollment_id: str
    manager_id: str
    state: EnrollmentState
    normalized_endpoint: str
    transport_profile_id: str
    discovery_result_id: str | None
    replace_agent_id: str | None
    requested_display_name: str | None
    ssh_user: str | None
    ssh_host: str | None
    ssh_port: int | None
    enrollment_method: EnrollmentMethod
    remote_instance_id: str | None
    remote_credential_id: str | None
    credential_temp_ref: str | None
    old_credential_ref: str | None
    old_remote_credential_id: str | None
    save_requested: bool
    expires_at: datetime
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime
    recovery_owner: str | None = None
    recovery_lease_until: datetime | None = None
    recovery_revision: int = 0
    validated_http_address: str | None = None
    cli_resume_nonce: str | None = None
    cli_peer_uid: int | None = None
    cli_input_fingerprint: str | None = None
    cli_pinned_address: str | None = None
    cli_accept_receipt: str | None = None
    old_normalized_endpoint: str | None = None
    old_transport_profile_id: str | None = None
    old_instance_id: str | None = None
    old_registry_revision: int | None = None
    old_enrollment_method: EnrollmentMethod | None = None
    old_source: str | None = None
    old_enabled: bool | None = None
    old_display_name: str | None = None


@dataclass(frozen=True)
class AgentRemovalJob:
    removal_id: str
    agent_id: str
    captured_revision: int
    credential_ref: str
    remote_credential_id: str | None
    normalized_endpoint: str
    transport_profile_id: str
    enrollment_method: EnrollmentMethod
    phase: str
    local_only: bool
    audit_event_id: int
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime
