from datetime import datetime
from typing import Protocol

from ic_env_guard.logs.models import LogSource


class LogSourceRepository(Protocol):
    def get(self, log_id: str) -> LogSource | None: ...

    def compare_and_swap(
        self,
        record: LogSource,
        expected_observed_at: datetime | None,
    ) -> bool: ...

    def list(self) -> tuple[LogSource, ...]: ...

    def delete_expired(self, cutoff: datetime, limit: int) -> int: ...
