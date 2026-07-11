from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol

from ic_env_guard.services.manager import ServiceManager
from ic_env_guard.terminal.manager import TerminalManager


class ObservationCounter(Protocol):
    def counts(self, now: datetime) -> tuple[int, int, int, int]: ...


class LogCounter(Protocol):
    def counts(self, now: datetime) -> tuple[int, int]: ...


@dataclass(frozen=True)
class ObservationCounts:
    total: int
    warning: int
    critical: int
    stale: int


@dataclass(frozen=True)
class LogCounts:
    total: int
    stale: int


@dataclass(frozen=True)
class ServiceCounts:
    total: int
    running: int
    unhealthy: int


@dataclass(frozen=True)
class TerminalCounts:
    active: int


@dataclass(frozen=True)
class AgentSummary:
    observed_at: datetime
    observations: ObservationCounts
    logs: LogCounts
    services: ServiceCounts
    terminals: TerminalCounts

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["observed_at"] = (
            self.observed_at.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
        return result


class SummaryService:
    def __init__(
        self,
        observations: ObservationCounter,
        logs: LogCounter,
        services: ServiceManager,
        terminals: TerminalManager,
    ) -> None:
        self._observations = observations
        self._logs = logs
        self._services = services
        self._terminals = terminals

    def get(self, *, now: datetime) -> AgentSummary:
        current = now.astimezone(UTC)
        observation_counts = self._observations.counts(current)
        log_counts = self._logs.counts(current)
        services = self._services.list_services()
        terminals = self._terminals.list()
        return AgentSummary(
            observed_at=current,
            observations=ObservationCounts(*observation_counts),
            logs=LogCounts(*log_counts),
            services=ServiceCounts(
                total=len(services),
                running=sum(item["status"] == "running" for item in services),
                unhealthy=sum(item["health_status"] == "unhealthy" for item in services),
            ),
            terminals=TerminalCounts(active=sum(item.status == "running" for item in terminals)),
        )
