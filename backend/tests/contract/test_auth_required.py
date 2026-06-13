import pytest
from fastapi.testclient import TestClient

from ic_env_guard.main import create_app


@pytest.fixture
def client(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    app = create_app(token_file=token_file)
    return TestClient(app)


@pytest.mark.contract
@pytest.mark.security
def test_privileged_routes_reject_missing_bearer_token(client):
    for method, path in [
        ("post", "/api/terminals"),
        ("get", "/api/terminals"),
        ("post", "/api/services/demo/start"),
        ("post", "/api/services/demo/stop"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 401
        assert response.json()["error"] == "unauthorized"


@pytest.mark.contract
@pytest.mark.security
def test_privileged_routes_reject_invalid_bearer_token(client):
    response = client.post(
        "/api/terminals",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"
