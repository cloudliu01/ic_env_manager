from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.config.models import AppConfig, AuthConfig
from ic_env_guard.main import create_app

NOW = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
MANAGER_ID = "2b576727-4f36-4f08-b90b-e8cbe98ebc80"


@pytest.fixture
def app_client(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("admin-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    config = AppConfig(auth=AuthConfig(token_file=token_file))
    app = create_app(
        token="admin-token",
        config=config,
        state_database=tmp_path / "state.db",
        instance_id_path=tmp_path / "instance-id",
    )
    return app, TestClient(app)


@pytest.mark.contract
@pytest.mark.security
def test_pending_has_minimum_permissions_then_active_gets_manager_actor(app_client):
    app, client = app_client
    issued = app.state.container.enrollment_service.issue_pending(
        MANAGER_ID, "enrollment-1", now=datetime.now(UTC)
    )
    headers = {"Authorization": f"Bearer {issued.token}"}

    capabilities = client.get("/api/v2/capabilities", headers=headers)
    assert capabilities.status_code == 200
    assert "manager-enrollment.v1" in capabilities.json()["capabilities"]
    assert client.get("/api/v2/summary", headers=headers).status_code == 200
    assert client.get("/api/v2/manager-credentials", headers=headers).status_code == 403
    assert client.get("/api/terminals", headers=headers).status_code == 401
    assert client.post("/api/terminals", headers=headers).status_code == 401
    assert client.post("/api/services/demo/start", headers=headers).status_code == 401

    response = client.post(
        f"/api/v2/manager-credentials/{issued.credential_id}/activate",
        headers=headers,
        json={"enrollment_id": "enrollment-1"},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "active"

    login = client.post("/api/auth/login", json={"token": issued.token})
    assert login.status_code == 200
    assert login.json()["actor"] == f"manager:{MANAGER_ID}"


@pytest.mark.contract
@pytest.mark.security
def test_metadata_never_exposes_token_or_hash_and_enforces_revoke_ownership(app_client):
    app, client = app_client
    service = app.state.container.enrollment_service
    first = service.issue_pending(MANAGER_ID, "enrollment-1", now=NOW)
    service.activate(first.credential_id, "enrollment-1", first.token, now=NOW)
    foreign = service.issue_pending(
        "f54e933c-925c-46d4-a4f4-2638ce7c0651", "enrollment-2", now=NOW
    )
    service.activate(foreign.credential_id, "enrollment-2", foreign.token, now=NOW)
    manager_headers = {"Authorization": f"Bearer {first.token}"}

    listed = client.get("/api/v2/manager-credentials", headers=manager_headers)
    assert listed.status_code == 403
    local_list = client.get(
        "/api/v2/manager-credentials",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert local_list.status_code == 200
    body = local_list.text
    for secret in (first.token, foreign.token, "token_hash", "token"):
        assert secret not in body

    denied = client.delete(
        f"/api/v2/manager-credentials/{foreign.credential_id}", headers=manager_headers
    )
    assert denied.status_code == 403
    allowed = client.delete(
        f"/api/v2/manager-credentials/{first.credential_id}", headers=manager_headers
    )
    assert allowed.status_code == 200
    repeated = client.delete(
        f"/api/v2/manager-credentials/{first.credential_id}",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert repeated.status_code == 200
    assert set(repeated.json()) == {"credential_id", "state"}


@pytest.mark.contract
@pytest.mark.security
def test_ingest_app_never_invokes_manager_credential_verifier(app_client, monkeypatch):
    from ic_env_guard.main import create_ingest_app

    app, _ = app_client
    monkeypatch.setattr(
        app.state.container.enrollment_service,
        "authenticate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("verifier called")),
    )
    ingest = TestClient(create_ingest_app(app.state.container), client=("127.0.0.1", 50000))
    response = ingest.put(
        "/api/v2/observations",
        json={
            "namespace": "eda",
            "name": "alive",
            "kind": "gauge",
            "value": 1,
            "status": "ok",
            "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "ttl_seconds": 60,
        },
        headers={"Authorization": "Bearer any-manager-token"},
    )
    assert response.status_code in (200, 201)
