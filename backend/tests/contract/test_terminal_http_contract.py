import pytest
from fastapi.testclient import TestClient

from ic_env_guard.main import create_app


@pytest.fixture
def client(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return TestClient(create_app(token_file=token_file))


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer secret-token"}


@pytest.mark.contract
def test_terminal_create_list_detail_history_resize_and_close_contract(client, auth_headers):
    created = client.post(
        "/api/terminals", headers=auth_headers, json={"title": "demo", "rows": 24, "cols": 80}
    )
    assert created.status_code == 201
    terminal = created.json()
    terminal_id = terminal["id"]
    assert terminal["status"] in {"running", "exited"}
    assert terminal["idle_timeout_minutes"] == 60

    listed = client.get("/api/terminals", headers=auth_headers)
    assert listed.status_code == 200
    assert any(item["id"] == terminal_id for item in listed.json()["terminals"])

    detail = client.get(f"/api/terminals/{terminal_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == terminal_id

    history = client.get(f"/api/terminals/{terminal_id}/history?cursor=0", headers=auth_headers)
    assert history.status_code == 200
    assert history.json()["terminal_id"] == terminal_id
    assert "truncated" in history.json()

    resized = client.post(
        f"/api/terminals/{terminal_id}/resize",
        headers=auth_headers,
        json={"rows": 40, "cols": 120},
    )
    assert resized.status_code == 204

    closed = client.delete(f"/api/terminals/{terminal_id}", headers=auth_headers)
    assert closed.status_code == 202
    assert closed.json()["status"] in {"closed", "exited"}


@pytest.mark.contract
def test_terminal_connect_token_contract(client, auth_headers):
    terminal = client.post("/api/terminals", headers=auth_headers, json={}).json()

    response = client.post(
        f"/api/terminals/{terminal['id']}/connect-token",
        headers=auth_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["ticket"]
    assert body["expires_in_seconds"] <= 60


@pytest.mark.contract
def test_terminal_unknown_session_returns_not_found(client, auth_headers):
    response = client.get("/api/terminals/missing", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
