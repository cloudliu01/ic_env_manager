from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.config.models import AppConfig, AuthConfig
from ic_env_guard.main import create_app, create_ingest_app
from ic_env_guard.observations.models import ObservationInput, ObservationStorageError


def _token(tmp_path):
    token = tmp_path / "token"
    token.write_text("secret-token\n", encoding="utf-8")
    token.chmod(0o600)
    return token


@pytest.mark.contract
def test_summary_is_authenticated_bounded_and_not_exposed_on_ingest(tmp_path):
    token = _token(tmp_path)
    config = AppConfig(auth=AuthConfig(token_file=token))
    app = create_app(config=config, state_database=tmp_path / "state.db")
    container = app.state.container
    now = datetime.now(UTC)
    container.observation_service.upsert(
        ObservationInput.model_validate(
            {
                "namespace": "eda",
                "name": "license_alive",
                "kind": "status",
                "status": "critical",
                "observed_at": now,
                "ttl_seconds": 120,
            }
        ),
        now=now,
    )

    client = TestClient(app)
    assert client.get("/api/v2/summary").status_code == 401
    response = client.get("/api/v2/summary", headers={"Authorization": "Bearer secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["observations"] == {"total": 1, "warning": 0, "critical": 1, "stale": 0}
    assert body["logs"] == {"total": 0, "stale": 0}
    assert body["services"] == {"total": 0, "running": 0, "unhealthy": 0}
    assert body["terminals"] == {"active": 0}
    assert set(body) == {"observed_at", "observations", "logs", "services", "terminals"}
    assert TestClient(create_ingest_app(container)).get("/api/v2/summary").status_code == 404


@pytest.mark.contract
def test_capabilities_advertise_observability_contracts(tmp_path):
    client = TestClient(create_app(token_file=_token(tmp_path)))
    response = client.get("/api/v2/capabilities", headers={"Authorization": "Bearer secret-token"})

    assert {"observations.v2", "logs.v2", "summary.v2"} <= set(response.json()["capabilities"])


@pytest.mark.contract
def test_summary_returns_stable_storage_error(tmp_path, monkeypatch):
    app = create_app(token_file=_token(tmp_path))
    monkeypatch.setattr(
        app.state.container.summary_service._observations,
        "counts",
        lambda now: (_ for _ in ()).throw(ObservationStorageError("sensitive database failure")),
    )

    response = TestClient(app).get(
        "/api/v2/summary", headers={"Authorization": "Bearer secret-token"}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert "sensitive" not in response.text
