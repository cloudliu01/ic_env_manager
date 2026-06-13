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
def test_login_accepts_generated_local_bearer_token(client):
    response = client.post("/api/auth/login", json={"token": "secret-token"})

    assert response.status_code == 200
    assert response.json() == {"actor": "local-admin", "token_type": "bearer"}


@pytest.mark.contract
@pytest.mark.security
def test_login_rejects_invalid_token_without_echoing_secret(client):
    response = client.post("/api/auth/login", json={"token": "wrong-token"})

    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "unauthorized"
    assert "wrong-token" not in str(body)


@pytest.mark.contract
def test_logout_requires_authentication(client):
    missing = client.post("/api/auth/logout")
    assert missing.status_code == 401

    ok = client.post("/api/auth/logout", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 204
