import sqlite3
import time
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from ic_env_guard.api.agent_enrollments import get_enrollment_orchestrator
from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.enrollment.agent_client import EnrollmentValidation
from ic_env_guard.enrollment.ssh import EnrollmentHelperResult
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
    assert created.json()["state"] == "awaiting_cli"
    assert created.json()["last_error_code"] == "ssh_unavailable"
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


def test_enrollment_create_rejects_ssh_option_injection_before_job_creation(tmp_path):
    client = TestClient(manager_app(tmp_path))
    body = enrollment_body()
    body["ssh"]["host"] = "-oProxyCommand=attacker"

    response = client.post("/api/v2/agent-enrollments", headers=AUTH, json=body)

    assert response.status_code == 422
    assert "ProxyCommand" not in response.text


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


def test_create_schedules_auto_with_request_audit_context_and_returns_immediately(tmp_path):
    app = manager_app(tmp_path)
    seen = []

    class Result:
        def to_public_dict(self):
            return {
                "enrollment_id": "22222222-2222-4222-8222-222222222222",
                "state": "running",
                "expires_at": "2026-07-12T12:10:00Z",
                "last_error_code": None,
                "preview": {"agent": None, "phases": {}},
            }

    class Orchestrator:
        def create_auto(self, request, context):
            seen.append((request, context))
            return Result()

    app.dependency_overrides[get_enrollment_orchestrator] = lambda: Orchestrator()
    response = TestClient(app).post(
        "/api/v2/agent-enrollments",
        headers={**AUTH, "X-Correlation-ID": "corr-auto-create"},
        json=enrollment_body(),
    )

    assert response.status_code == 201
    assert response.json()["state"] == "running"
    request, context = seen[0]
    assert request.enrollment_method.value == "ssh_auto"
    assert context.actor_id == "local-admin"
    assert context.source_addr == "testclient"
    assert context.correlation_id == "corr-auto-create"


def test_create_and_background_auto_have_separate_durable_audit_events(
    tmp_path, monkeypatch
):
    async def healthy(self):
        self.healthy = True
        return True

    async def issue(self, request, _profile):
        return EnrollmentHelperResult(
            instance_id="33333333-3333-4333-8333-333333333333",
            credential_id="44444444-4444-4444-8444-444444444444",
            token=b"eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg",
            expires_at=min(request.expires_at, datetime.now(UTC) + timedelta(minutes=5)),
            validation_target=type(
                "ValidationTarget",
                (),
                {
                    "normalized_endpoint": request.base_url,
                    "pinned_address": "10.20.30.40",
                },
            )(),
        )

    async def validate_pending(self, target, _token, *, helper_instance_id):
        return EnrollmentValidation(
            normalized_endpoint=target.normalized_endpoint,
            api_version="2",
            agent_version="0.3.0",
            capabilities=("manager-enrollment.v1", "summary.v2"),
            instance_id=helper_instance_id,
            summary=None,
            readiness_warning="agent_readiness_unavailable",
        )

    monkeypatch.setattr(
        "ic_env_guard.enrollment.ssh.SshEnrollmentAdapter.check_available", healthy
    )
    monkeypatch.setattr(
        "ic_env_guard.enrollment.ssh.SshEnrollmentAdapter.issue", issue
    )
    monkeypatch.setattr(
        "ic_env_guard.enrollment.agent_client.EnrollmentAgentClient.validate_pending",
        validate_pending,
    )
    token_file = tmp_path / "manager.token"
    token_file.write_text("manager-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    database = tmp_path / "manager.db"
    config = AppConfig(
        mode="control-plane",
        auth=AuthConfig(token_file=token_file),
        control_plane=ControlPlaneConfig(
            audit_database=database,
            allowed_agent_cidrs=["10.0.0.0/8"],
        ),
    )

    with TestClient(create_app(config=config)) as client:
        created = client.post(
            "/api/v2/agent-enrollments", headers=AUTH, json=enrollment_body()
        )
        enrollment_id = created.json()["enrollment_id"]
        for _ in range(50):
            current = client.get(
                f"/api/v2/agent-enrollments/{enrollment_id}", headers=AUTH
            ).json()
            if current["state"] == "verified":
                break
            time.sleep(0.01)

    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT operation, result, dispatch_state FROM control_plane_audit_events "
            "WHERE target IN (?, ?) ORDER BY id",
            ("enrollment:new", f"enrollment:{enrollment_id}"),
        ).fetchall()
    finally:
        connection.close()
    assert created.status_code == 201
    assert created.json()["state"] == "running"
    assert current["state"] == "verified"
    assert rows == [
        ("agent-enrollment.create", "success", "not_dispatched"),
        ("agent-enrollment.ssh-auto", "success", "dispatched"),
    ]
