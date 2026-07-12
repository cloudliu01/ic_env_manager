from datetime import datetime
from typing import Protocol
from uuid import UUID

from ic_env_guard.fleet.models import (
    AgentPage,
    AgentQuery,
    AgentRecord,
    AgentStatus,
    EnrollmentJob,
    EnrollmentState,
)


class ManagerRegistryRepository(Protocol):
    def get_or_create_manager_id(self) -> UUID: ...

    def create(self, record: AgentRecord) -> AgentRecord: ...

    def get(self, agent_id: str) -> AgentRecord | None: ...

    def list(self, query: AgentQuery) -> AgentPage: ...

    def update_if_revision(
        self,
        record: AgentRecord,
        expected_revision: int,
        *,
        owner_enrollment_id: str | None = None,
        owner_removal_id: str | None = None,
    ) -> AgentRecord: ...

    def delete(
        self,
        agent_id: str,
        *,
        owner_enrollment_id: str | None = None,
        owner_removal_id: str | None = None,
    ) -> None: ...

    def credential_references(self) -> set[str]: ...


class AgentStatusRepository(Protocol):
    def get(self, agent_id: str) -> AgentStatus | None: ...

    def update_if_target_revision(
        self, observation: AgentStatus, expected_revision: int
    ) -> bool: ...

    def update_many_if_target_revisions(
        self, observations: tuple[AgentStatus, ...]
    ) -> bool: ...


class EnrollmentJournalRepository(Protocol):
    def create(self, job: EnrollmentJob) -> EnrollmentJob: ...

    def get(self, enrollment_id: str) -> EnrollmentJob | None: ...

    def set_state(
        self,
        enrollment_id: str,
        state: EnrollmentState,
        updated_at: datetime,
        *,
        expected_state: EnrollmentState,
    ) -> None: ...

    def consume_rotation(
        self,
        enrollment_id: str,
        *,
        agent_id: str,
        display_name: str,
        now: datetime,
    ) -> EnrollmentJob: ...

    def mark_recovery_claim_error(
        self,
        enrollment_id: str,
        *,
        owner: str,
        expected_revision: int,
        error_code: str,
        now: datetime,
    ) -> bool: ...

    def recovery_credential_references(self) -> set[str]: ...
