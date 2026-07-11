from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.config.models import AppConfig, AuthConfig, LogsConfig
from ic_env_guard.main import create_ingest_app, create_public_app


def _apps(tmp_path):
    root = tmp_path / "logs"
    root.mkdir()
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config = AppConfig(
        auth=AuthConfig(token_file=token_file),
        logs=LogsConfig(allowed_roots=[root]),
    )
    container = build_agent_container(config, tmp_path / "state.db")
    return (
        root,
        container,
        TestClient(create_ingest_app(container), client=("127.0.0.1", 50000)),
        TestClient(create_public_app(container)),
    )


def _payload(path, observed_at, **changes):
    value = {
        "path": str(path),
        "last_updated": observed_at.isoformat().replace("+00:00", "Z"),
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "ttl_seconds": 120,
    }
    value.update(changes)
    return value


@pytest.mark.contract
def test_log_ingest_creates_updates_and_is_not_exposed_by_public_listener(tmp_path):
    root, container, ingest, public = _apps(tmp_path)
    path = root / "run.log"
    path.write_text("first\n", encoding="utf-8")
    now = datetime.now(UTC)

    created = ingest.put("/api/v2/logs/run-log", json=_payload(path, now))
    idempotent = ingest.put("/api/v2/logs/run-log", json=_payload(path, now))
    updated = ingest.put(
        "/api/v2/logs/run-log",
        json=_payload(path, now + timedelta(seconds=1)),
    )
    hidden = public.put("/api/v2/logs/run-log", json=_payload(path, now))

    assert created.status_code == 201
    assert idempotent.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["id"] == "run-log"
    assert updated.json()["path"] == str(path.resolve())
    assert updated.json()["producer_id"] == "local"
    assert hidden.status_code == 404
    container.database_engine.dispose()


@pytest.mark.contract
def test_log_ingest_validates_id_identity_and_time_conflicts(tmp_path):
    root, container, ingest, _ = _apps(tmp_path)
    path = root / "run.log"
    path.write_text("first\n", encoding="utf-8")
    now = datetime.now(UTC)
    assert ingest.put("/api/v2/logs/run-log", json=_payload(path, now)).status_code == 201

    invalid_id = ingest.put("/api/v2/logs/Bad-ID", json=_payload(path, now))
    producer_body = ingest.put(
        "/api/v2/logs/other", json=_payload(path, now, producer_id="spoofed")
    )
    producer_header = ingest.put(
        "/api/v2/logs/other",
        json=_payload(path, now),
        headers={"Producer-ID": "spoofed"},
    )
    stale = ingest.put(
        "/api/v2/logs/run-log", json=_payload(path, now - timedelta(seconds=1))
    )
    conflict = ingest.put(
        "/api/v2/logs/run-log", json=_payload(path, now, ttl_seconds=121)
    )

    assert invalid_id.status_code == 422
    assert producer_body.status_code == 422
    assert producer_header.status_code == 422
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_log_source"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "log_source_timestamp_conflict"
    container.database_engine.dispose()


@pytest.mark.contract
@pytest.mark.security
def test_log_ingest_uses_actual_loopback_peer_and_ignores_forwarding_headers(tmp_path):
    root, container, _, _ = _apps(tmp_path)
    path = root / "run.log"
    path.write_text("first\n", encoding="utf-8")
    remote = TestClient(
        create_ingest_app(container), client=("198.51.100.10", 50000)
    )

    response = remote.put(
        "/api/v2/logs/run-log",
        json=_payload(path, datetime.now(UTC)),
        headers={
            "Forwarded": "for=127.0.0.1",
            "X-Forwarded-For": "127.0.0.1",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ingest_peer_forbidden"
    container.database_engine.dispose()
