from datetime import datetime
from typing import Protocol

from ic_env_guard.observations.models import Observation, ObservationPage, ObservationQuery


class ObservationRepository(Protocol):
    def get(self, identity_key: str) -> Observation | None: ...

    def compare_and_swap(
        self,
        record: Observation,
        expected_observed_at: datetime | None,
    ) -> bool: ...

    def list(self, query: ObservationQuery) -> ObservationPage: ...

    def list_all(self) -> tuple[Observation, ...]: ...

    def counts(self, now: datetime) -> tuple[int, int, int, int]: ...

    def delete_expired(self, cutoff: datetime, limit: int) -> int: ...
