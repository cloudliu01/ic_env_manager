import pytest
from fastapi.testclient import TestClient

from ic_env_guard.api.audit_health import AuditStorageHealth, get_audit_storage_health
from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.main import create_app


@pytest.fixture
def client(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return TestClient(create_app(token_file=token_file))


@pytest.mark.contract
def test_healthz_returns_liveness(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-IC-Env-Guard-Agent"] == "2"


@pytest.mark.contract
def test_manager_healthz_cannot_be_fingerprinted_as_agent(tmp_path):
    token = tmp_path / "manager.token"
    token.write_text("manager-secret\n", encoding="utf-8")
    token.chmod(0o600)
    manager = TestClient(
        create_app(
            config=AppConfig(
                mode="control-plane",
                auth=AuthConfig(token_file=token),
                control_plane=ControlPlaneConfig(
                    audit_database=tmp_path / "manager.db"
                ),
            )
        )
    )
    response = manager.get("/healthz")
    assert response.status_code == 200
    assert "X-IC-Env-Guard-Agent" not in response.headers


@pytest.mark.contract
def test_readyz_returns_readiness_details(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["config_loaded"] is True
    assert body["security_valid"] is True
    assert body["audit_storage"] == "ok"


@pytest.mark.contract
def test_readyz_degrades_when_audit_storage_is_unavailable(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    audit_health = AuditStorageHealth(healthy=False)
    app = create_app(token_file=token_file)
    app.dependency_overrides[get_audit_storage_health] = lambda: audit_health

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "config_loaded": True,
        "security_valid": True,
        "audit_storage": "unavailable",
    }
