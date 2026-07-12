from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.fleet.models import AgentRecord, AgentStatus, EnrollmentMethod
from ic_env_guard.main import create_app

AUTH = {"Authorization": "Bearer manager-secret"}


@pytest.mark.contract
def test_fleet_overview_returns_all_cached_agents_sorted_without_secrets(tmp_path):
    token_file = tmp_path / "manager.token"
    token_file.write_text("manager-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    app = create_app(
        config=AppConfig(
            mode="control-plane",
            auth=AuthConfig(token_file=token_file),
            control_plane=ControlPlaneConfig(audit_database=tmp_path / "manager.db"),
        )
    )
    container = app.state.container
    now = datetime.now(UTC)
    for agent_id, name, connection, workload in (
        ("ready", "Zeta", "ready", "healthy"),
        ("down", "Alpha", "unavailable", "unknown"),
    ):
        with container.credential_store.lifecycle_lease():
            credential_ref = container.credential_store.put(f"token-{agent_id}".encode())
        container.registry_repository.create(
            AgentRecord(
                agent_id=agent_id,
                instance_id=None,
                display_name=name,
                normalized_endpoint=f"https://10.0.0.{10 + len(agent_id)}:8765",
                credential_ref=credential_ref,
                remote_credential_id=None,
                transport_profile_id="system-tls",
                enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
                enabled=True,
                source="config_import",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        container.status_repository.update_if_target_revision(
            AgentStatus(
                agent_id=agent_id,
                target_revision=1,
                connection_status=connection,
                workload_status=workload,
                observed_at=now,
                stale_after=now + timedelta(minutes=1),
                api_version="2",
                agent_version="0.2.0",
                capabilities=("summary.v2",),
                summary={"observations": {"total": 1}},
                last_error_code=("agent_network_error" if connection == "unavailable" else None),
                updated_at=now,
            ),
            expected_revision=1,
        )

    response = TestClient(app).get("/api/v2/fleet/overview", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["collected_at"]
    assert [item["agent_id"] for item in body["agents"]] == ["down", "ready"]
    assert body["agents"][0]["last_error_code"] == "agent_network_error"
    assert "summary" in body["agents"][0]
    assert "credential" not in response.text
    assert "token-down" not in response.text
