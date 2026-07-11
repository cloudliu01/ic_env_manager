import pytest
from fastapi.testclient import TestClient

from ic_env_guard.main import create_app


@pytest.fixture
def agent_client(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return TestClient(create_app(token_file=token_file))


@pytest.mark.contract
def test_v1_capabilities_and_terminal_routes_remain_available(agent_client):
    headers = {"Authorization": "Bearer secret-token"}
    capabilities = agent_client.get("/api/capabilities", headers=headers)
    services = agent_client.get("/api/services", headers=headers)
    created = agent_client.post(
        "/api/terminals",
        headers=headers,
        json={"title": "Compatibility shell", "rows": 24, "cols": 80},
    )

    assert capabilities.status_code == 200
    assert capabilities.json()["api_version"] == "1"
    assert "services.v1" in capabilities.json()["capabilities"]
    assert "terminals.v1" in capabilities.json()["capabilities"]
    assert services.status_code == 200
    assert "services" in services.json()
    assert created.status_code == 201
    assert created.json()["title"] == "Compatibility shell"

    terminal_id = created.json()["id"]
    closed = agent_client.delete(f"/api/terminals/{terminal_id}", headers=headers)
    assert closed.status_code == 202


@pytest.mark.contract
def test_v1_routes_keep_bearer_token_semantics(agent_client):
    for method, path in [
        ("get", "/api/capabilities"),
        ("get", "/api/services"),
        ("post", "/api/terminals"),
    ]:
        missing = getattr(agent_client, method)(path)
        invalid = getattr(agent_client, method)(
            path, headers={"Authorization": "Bearer wrong-token"}
        )

        assert missing.status_code == 401
        assert missing.json()["error"] == "unauthorized"
        assert invalid.status_code == 401
        assert invalid.json()["error"] == "unauthorized"
