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
        self, record: AgentRecord, expected_revision: int
    ) -> AgentRecord: ...

    def delete(self, agent_id: str) -> None: ...

    def credential_references(self) -> set[str]: ...


class AgentStatusRepository(Protocol):
    def get(self, agent_id: str) -> AgentStatus | None: ...

    def update_if_target_revision(
        self, observation: AgentStatus, expected_revision: int
    ) -> bool: ...


class EnrollmentJournalRepository(Protocol):
    def create(self, job: EnrollmentJob) -> EnrollmentJob: ...

    def get(self, enrollment_id: str) -> EnrollmentJob | None: ...

    def set_state(
        self, enrollment_id: str, state: EnrollmentState, updated_at: datetime
    ) -> None: ...

    def non_terminal_credential_references(self) -> set[str]: ...
