from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Protocol

from prometheus_client.core import Metric

from ic_env_guard.logs.models import LogSource
from ic_env_guard.observations.models import Observation


class ObservationReader(Protocol):
    def list_all(self) -> tuple[Observation, ...]: ...


class LogReader(Protocol):
    def list(self) -> tuple[LogSource, ...]: ...


class ObservabilityCollector:
    def __init__(
        self,
        observations: ObservationReader,
        logs: LogReader,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._observations = observations
        self._logs = logs
        self._clock = clock

    def collect(self) -> Iterable[Metric]:
        now = self._clock().astimezone(UTC)
        value = Metric("ic_env_observation_value", "Fresh observation value", "gauge")
        status = Metric("ic_env_observation_status", "Fresh observation status", "gauge")
        emitted_statuses: set[tuple[str, str, str]] = set()
        for record in self._observations.list_all():
            if record.is_stale(now):
                continue
            base_labels = {"namespace": record.namespace, "name": record.name}
            if record.kind in ("gauge", "counter") and record.value is not None:
                value.add_sample(
                    "ic_env_observation_value",
                    value=float(record.value),
                    labels={**record.labels, **base_labels},
                )
            status_key = (record.namespace, record.name, record.status)
            if status_key not in emitted_statuses:
                status.add_sample(
                    "ic_env_observation_status",
                    value=1.0,
                    labels={**base_labels, "status": record.status},
                )
                emitted_statuses.add(status_key)
        yield value
        yield status

        last_updated = Metric(
            "ic_env_log_source_last_updated_seconds",
            "Registered log source last update time",
            "gauge",
        )
        stale = Metric("ic_env_log_source_stale", "Registered log source staleness", "gauge")
        for record in self._logs.list():
            labels = {"log_id": record.id}
            last_updated.add_sample(
                "ic_env_log_source_last_updated_seconds",
                value=record.last_updated.timestamp(),
                labels=labels,
            )
            stale.add_sample(
                "ic_env_log_source_stale",
                value=1.0 if record.is_stale(now) else 0.0,
                labels=labels,
            )
        yield last_updated
        yield stale
