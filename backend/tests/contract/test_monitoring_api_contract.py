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
def test_monitoring_api_requires_auth(client):
    response = client.get("/api/monitoring/local")

    assert response.status_code == 401


@pytest.mark.contract
def test_local_monitoring_snapshot_contract(client, auth_headers):
    response = client.get("/api/monitoring/local", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["host_id"] == "local"
    assert body["status"] == "online"
    assert "sampled_at" in body
    assert body["cpu"]["percent"] >= 0
    assert body["memory"]["total_bytes"] > 0
    assert body["disks"]
    assert body["uptime_seconds"] >= 0


@pytest.mark.contract
def test_machine_crud_does_not_return_key(client, auth_headers):
    create = client.post(
        "/api/monitoring/machines",
        headers=auth_headers,
        json={"name": "Lab host", "address": "127.0.0.1", "port": 8765, "key": "remote-key"},
    )

    assert create.status_code == 201
    machine = create.json()
    assert machine["name"] == "Lab host"
    assert machine["address"] == "127.0.0.1"
    assert machine["port"] == 8765
    assert "key" not in machine

    listed = client.get("/api/monitoring/machines", headers=auth_headers).json()["machines"]
    assert [item["id"] for item in listed] == ["local", machine["id"]]
    assert all("key" not in item for item in listed)

    delete = client.delete(f"/api/monitoring/machines/{machine['id']}", headers=auth_headers)
    assert delete.status_code == 204
    listed = client.get("/api/monitoring/machines", headers=auth_headers).json()["machines"]
    assert [item["id"] for item in listed] == ["local"]


@pytest.mark.contract
def test_local_machine_cannot_be_deleted(client, auth_headers):
    response = client.delete("/api/monitoring/machines/local", headers=auth_headers)

    assert response.status_code == 400
    assert response.json()["error"] == "machine_not_deletable"


@pytest.mark.contract
def test_machine_address_rejects_urls(client, auth_headers):
    response = client.post(
        "/api/monitoring/machines",
        headers=auth_headers,
        json={"address": "http://127.0.0.1/path", "port": 8765, "key": "remote-key"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_machine"
