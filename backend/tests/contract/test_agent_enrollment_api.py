from fastapi.testclient import TestClient

from ic_env_guard.api.agent_enrollments import get_enrollment_orchestrator
from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.main import create_app

AUTH = {"Authorization": "Bearer manager-secret"}
PHASES = {
    "network",
    "ssh",
    "transport",
    "authentication",
    "protocol",
    "identity",
    "capabilities",
    "readiness",
}


def manager_app(tmp_path):
    token_file = tmp_path / "manager.token"
    token_file.write_text("manager-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    return create_app(
        config=AppConfig(
            mode="control-plane",
            auth=AuthConfig(token_file=token_file),
            control_plane=ControlPlaneConfig(audit_database=tmp_path / "manager.db"),
        )
    )


def enrollment_body():
    return {
        "base_url": "https://10.20.30.40:8765",
        "display_name": "Lab 01",
        "transport_profile_id": "system-tls",
        "ssh": {"user": "edaops", "host": "10.20.30.40", "port": 22},
    }


def test_enrollment_create_get_cancel_are_authenticated_and_safe(tmp_path):
    app = manager_app(tmp_path)
    client = TestClient(app)

    unauthorized = client.post("/api/v2/agent-enrollments", json=enrollment_body())
    created = client.post(
        "/api/v2/agent-enrollments", headers=AUTH, json=enrollment_body()
    )
    enrollment_id = created.json()["enrollment_id"]
    fetched = client.get(f"/api/v2/agent-enrollments/{enrollment_id}", headers=AUTH)
    cancelled = client.post(
        f"/api/v2/agent-enrollments/{enrollment_id}/cancel", headers=AUTH
    )

    assert unauthorized.status_code == 401
    assert created.status_code == 201
    assert created.json()["state"] == "pending"
    assert set(created.json()["preview"]["phases"]) == PHASES
    assert fetched.json() == created.json()
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    serialized = created.text + fetched.text + cancelled.text
    for forbidden in (
        "credential_ref",
        "token_file",
        "private_key",
        "SSH_AUTH_SOCK",
        "Authorization",
    ):
        assert forbidden not in serialized


def test_enrollment_not_found_and_cancel_conflict_are_stable_v2_errors(tmp_path):
    client = TestClient(manager_app(tmp_path))
    missing = client.get("/api/v2/agent-enrollments/missing", headers=AUTH)
    created = client.post(
        "/api/v2/agent-enrollments", headers=AUTH, json=enrollment_body()
    ).json()
    enrollment_id = created["enrollment_id"]
    client.post(f"/api/v2/agent-enrollments/{enrollment_id}/cancel", headers=AUTH)
    repeated = client.post(
        f"/api/v2/agent-enrollments/{enrollment_id}/cancel", headers=AUTH
    )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "agent_enrollment_not_found"
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "agent_enrollment_not_cancellable"


def test_legacy_validate_token_is_write_only_and_preview_degrades_identity(tmp_path, caplog):
    app = manager_app(tmp_path)
    seen = []

    class Result:
        def to_public_dict(self):
            return {
                "enrollment_id": "11111111-1111-4111-8111-111111111111",
                "state": "verified",
                "expires_at": "2026-07-12T12:10:00.000000Z",
                "preview": {
                    "agent": {
                        "agent_id": "11111111-1111-4111-8111-111111111111",
                        "instance_id": None,
                        "api_version": "1",
                        "agent_version": "0.2.0",
                        "capabilities": ["services.v1"],
                    },
                    "phases": {
                        name: {
                            "status": (
                                "warning" if name in {"identity", "readiness"} else "success"
                            ),
                            "code": (
                                f"legacy_{name}_unavailable"
                                if name in {"identity", "readiness"}
                                else None
                            ),
                        }
                        for name in PHASES
                    },
                },
            }

    class Orchestrator:
        async def validate_legacy(self, request, token):
            seen.append((request, token))
            return Result()

    app.dependency_overrides[get_enrollment_orchestrator] = lambda: Orchestrator()
    client = TestClient(app)
    secret = "legacy-secret-never-return"

    response = client.post(
        "/api/v2/agents/validate",
        headers=AUTH,
        json={
            "base_url": "https://10.20.30.40:8765",
            "transport_profile_id": "system-tls",
            "token": secret,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "verified"
    assert body["preview"]["agent"]["instance_id"] is None
    assert body["preview"]["phases"]["identity"]["status"] == "warning"
    assert body["preview"]["phases"]["readiness"]["status"] == "warning"
    assert seen[0][1] == secret
    assert secret not in response.text
    assert secret not in caplog.text
