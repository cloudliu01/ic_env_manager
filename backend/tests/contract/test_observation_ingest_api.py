from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.config.models import AppConfig, AuthConfig
from ic_env_guard.main import create_ingest_app


def _container(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config = AppConfig(auth=AuthConfig(token_file=token_file))
    return build_agent_container(config, tmp_path / "state.db")


def _payload(observed_at: datetime, **changes):
    payload = {
        "namespace": "eda",
        "name": "license_server_alive",
        "kind": "gauge",
        "value": 1,
        "unit": "boolean",
        "status": "ok",
        "message": "lmgrd is running",
        "labels": {"server": "license01"},
        "details": {"pid": 1234},
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "ttl_seconds": 120,
    }
    payload.update(changes)
    return payload


@pytest.mark.contract
def test_ingest_creates_updates_and_returns_normalized_record_without_token(tmp_path):
    container = _container(tmp_path)
    client = TestClient(create_ingest_app(container), client=("127.0.0.1", 50000))
    observed_at = datetime.now(UTC)

    created = client.put("/api/v2/observations", json=_payload(observed_at))
    idempotent = client.put("/api/v2/observations", json=_payload(observed_at))
    updated = client.put(
        "/api/v2/observations",
        json=_payload(observed_at + timedelta(seconds=1), value=0, status="warning"),
    )

    assert created.status_code == 201
    assert idempotent.status_code == 200
    assert updated.status_code == 200
    body = updated.json()
    assert len(body["identity_key"]) == 64
    assert body["producer_id"] == "local"
    assert body["details"] == {"pid": 1234}
    assert body["labels"] == {"server": "license01"}
    assert body["stale"] is False
    assert body["received_at"].endswith("Z")
    assert body["expires_at"].endswith("Z")
    assert updated.headers["X-Correlation-ID"]
    container.database_engine.dispose()


@pytest.mark.contract
def test_ingest_rejects_producer_identity_and_maps_domain_failures(tmp_path):
    container = _container(tmp_path)
    client = TestClient(create_ingest_app(container), client=("::1", 50000))
    now = datetime.now(UTC)

    producer = client.put(
        "/api/v2/observations", json=_payload(now, producer_id="pretend-producer")
    )
    producer_header = client.put(
        "/api/v2/observations",
        json=_payload(now, name="header_producer"),
        headers={"Producer-ID": "pretend-producer"},
    )
    expired = client.put(
        "/api/v2/observations",
        json=_payload(now - timedelta(minutes=5), name="expired", ttl_seconds=1),
    )
    future = client.put(
        "/api/v2/observations",
        json=_payload(now + timedelta(minutes=2), name="future"),
    )

    assert producer.status_code == 422
    assert producer.json()["error"]["code"] == "validation_error"
    assert producer_header.status_code == 422
    assert producer_header.json()["error"]["code"] == "validation_error"
    assert expired.status_code == 422
    assert expired.json()["error"]["code"] == "observation_expired"
    assert future.status_code == 422
    assert future.json()["error"]["code"] == "observation_in_future"
    container.database_engine.dispose()


@pytest.mark.contract
@pytest.mark.parametrize("reserved_label", ["namespace", "name", "status"])
def test_ingest_rejects_prometheus_reserved_observation_labels(tmp_path, reserved_label):
    container = _container(tmp_path)
    client = TestClient(create_ingest_app(container), client=("127.0.0.1", 50000))

    response = client.put(
        "/api/v2/observations",
        json=_payload(datetime.now(UTC), labels={reserved_label: "producer-value"}),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"] == "request validation failed"
    assert response.headers["X-Correlation-ID"] == response.json()["error"]["correlation_id"]
    container.database_engine.dispose()


@pytest.mark.contract
def test_ingest_rejects_stale_and_conflicting_timestamps(tmp_path):
    container = _container(tmp_path)
    client = TestClient(create_ingest_app(container), client=("127.0.0.1", 50000))
    observed_at = datetime.now(UTC)
    assert client.put("/api/v2/observations", json=_payload(observed_at)).status_code == 201

    stale = client.put(
        "/api/v2/observations",
        json=_payload(observed_at - timedelta(seconds=1)),
    )
    conflict = client.put(
        "/api/v2/observations", json=_payload(observed_at, value=0)
    )

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_observation"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "observation_timestamp_conflict"
    container.database_engine.dispose()
