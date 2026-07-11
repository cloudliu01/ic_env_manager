from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.bootstrap.composition import build_agent_container
from ic_env_guard.config.models import AppConfig, AuthConfig
from ic_env_guard.main import create_ingest_app, create_public_app


@pytest.mark.integration
def test_observation_round_trip_survives_agent_restart(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config = AppConfig(auth=AuthConfig(token_file=token_file))
    database = tmp_path / "state.db"
    payload = {
        "namespace": "eda",
        "name": "license_server_alive",
        "kind": "gauge",
        "value": 1,
        "status": "warning",
        "labels": {"server": "license01"},
        "details": {"pid": 1234, "features": ["compiler", "verdi"]},
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "ttl_seconds": 120,
    }

    first = build_agent_container(config, database, tmp_path / "instance-id")
    created = TestClient(
        create_ingest_app(first), client=("127.0.0.1", 50000)
    ).put("/api/v2/observations", json=payload)
    identity_key = created.json()["identity_key"]
    first.database_engine.dispose()

    restarted = build_agent_container(config, database, tmp_path / "instance-id")
    response = TestClient(create_public_app(restarted)).get(
        f"/api/v2/observations/{identity_key}",
        headers={"Authorization": "Bearer secret-token"},
    )

    assert created.status_code == 201
    assert response.status_code == 200
    assert response.json()["details"] == payload["details"]
    assert response.json()["producer_id"] == "local"
    restarted.database_engine.dispose()
