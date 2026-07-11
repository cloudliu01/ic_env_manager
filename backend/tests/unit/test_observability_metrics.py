from dataclasses import replace
from datetime import UTC, datetime, timedelta

from prometheus_client import CollectorRegistry, generate_latest

from ic_env_guard.logs.models import LogSource
from ic_env_guard.metrics.observability import ObservabilityCollector
from ic_env_guard.observations.models import Observation

NOW = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)


class ObservationReader:
    def __init__(self, records):
        self.records = tuple(records)

    def list_all(self):
        return self.records


class LogReader:
    def __init__(self, records):
        self.records = tuple(records)

    def list(self):
        return self.records


def _observation(*, expires_at=NOW + timedelta(seconds=120), kind="gauge", value=1):
    return Observation(
        identity_key="a" * 64,
        namespace="eda",
        name="license_alive",
        kind=kind,
        value=value,
        unit="secret-unit",
        status="warning",
        message="secret-message",
        labels={"server": "license01"},
        details={"password": "secret-detail"},
        observed_at=NOW,
        ttl_seconds=120,
        received_at=NOW,
        expires_at=expires_at,
        producer_id="local",
        updated_at=NOW,
    )


def _log(tmp_path, *, expires_at=NOW + timedelta(seconds=120)):
    return LogSource(
        id="run-log",
        path=tmp_path / "private" / "run.log",
        last_updated=NOW - timedelta(seconds=2),
        observed_at=NOW,
        ttl_seconds=120,
        received_at=NOW,
        expires_at=expires_at,
        producer_id="local",
        updated_at=NOW,
    )


def _scrape(tmp_path, observation, log, now):
    registry = CollectorRegistry()
    registry.register(
        ObservabilityCollector(
            ObservationReader([observation]), LogReader([log]), clock=lambda: now
        )
    )
    return generate_latest(registry).decode()


def test_projection_exports_only_safe_fresh_observation_and_log_labels(tmp_path):
    text = _scrape(tmp_path, _observation(), _log(tmp_path), NOW)

    assert (
        'ic_env_observation_value{name="license_alive",namespace="eda",server="license01"} 1.0'
    ) in text
    assert (
        'ic_env_observation_status{name="license_alive",namespace="eda",status="warning"} 1.0'
    ) in text
    assert 'ic_env_log_source_stale{log_id="run-log"} 0.0' in text
    assert "secret-unit" not in text
    assert "secret-message" not in text
    assert "secret-detail" not in text
    assert str(tmp_path) not in text


def test_projection_omits_stale_observation_samples_but_marks_stale_log(tmp_path):
    expired = NOW
    text = _scrape(
        tmp_path,
        _observation(expires_at=expired),
        _log(tmp_path, expires_at=expired),
        NOW,
    )

    assert "ic_env_observation_value{" not in text
    assert "ic_env_observation_status{" not in text
    assert 'ic_env_log_source_stale{log_id="run-log"} 1.0' in text


def test_status_kind_does_not_export_a_numeric_value(tmp_path):
    text = _scrape(tmp_path, _observation(kind="status", value=None), _log(tmp_path), NOW)

    assert "ic_env_observation_value{" not in text
    assert "ic_env_observation_status{" in text


def test_status_samples_are_unique_per_namespace_name_and_status(tmp_path):
    base = _observation()
    observations = ObservationReader(
        [
            base,
            replace(
                base,
                identity_key="b" * 64,
                labels={"server": "license02"},
            ),
            replace(
                base,
                identity_key="c" * 64,
                labels={"server": "license03"},
                status="critical",
            ),
            replace(
                base,
                identity_key="d" * 64,
                labels={"server": "license04"},
                status="critical",
                expires_at=NOW,
            ),
        ]
    )
    registry = CollectorRegistry()
    registry.register(ObservabilityCollector(observations, LogReader([]), clock=lambda: NOW))

    families = {metric.name: metric for metric in registry.collect()}
    status_samples = families["ic_env_observation_status"].samples
    value_samples = families["ic_env_observation_value"].samples

    assert [(sample.labels, sample.value) for sample in status_samples] == [
        ({"namespace": "eda", "name": "license_alive", "status": "warning"}, 1.0),
        ({"namespace": "eda", "name": "license_alive", "status": "critical"}, 1.0),
    ]
    assert {sample.labels["server"] for sample in value_samples} == {
        "license01",
        "license02",
        "license03",
    }
