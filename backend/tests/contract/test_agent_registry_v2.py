from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ic_env_guard.api.agent_registry import get_fleet_probe_service
from ic_env_guard.api.control_plane_audit import get_control_plane_audit_repository
from ic_env_guard.config.models import AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.fleet.models import AgentRecord, AgentStatus, EnrollmentMethod
from ic_env_guard.main import create_app

AUTH = {"Authorization": "Bearer manager-secret"}


def _manager(tmp_path):
    token_file = tmp_path / "manager.token"
    token_file.write_text("manager-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    config = AppConfig(
        mode="control-plane",
        auth=AuthConfig(token_file=token_file),
        control_plane=ControlPlaneConfig(
            audit_database=tmp_path / "manager.db",
            allowed_agent_cidrs=["10.0.0.0/8"],
        ),
    )
    app = create_app(config=config)
    return app, app.state.container


def _add(container, *, agent_id, name, endpoint, enabled=True, capabilities=()):
    now = datetime.now(UTC)
    with container.credential_store.lifecycle_lease():
        credential_ref = container.credential_store.put(f"token-{agent_id}".encode())
    record = AgentRecord(
        agent_id=agent_id,
        instance_id=None,
        display_name=name,
        normalized_endpoint=endpoint,
        credential_ref=credential_ref,
        remote_credential_id=None,
        transport_profile_id="system-tls",
        enrollment_method=EnrollmentMethod.LEGACY_ADMIN_TOKEN,
        enabled=enabled,
        source="config_import",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    container.registry_repository.create(record)
    container.status_repository.update_if_target_revision(
        AgentStatus(
            agent_id=agent_id,
            target_revision=1,
            connection_status="ready" if enabled else "disabled",
            workload_status="healthy" if enabled else "unknown",
            observed_at=now,
            stale_after=now + timedelta(minutes=5),
            api_version="2" if enabled else None,
            agent_version="0.2.0" if enabled else None,
            capabilities=capabilities,
            summary={"observations": {"critical": 0}},
            last_error_code=None,
            updated_at=now,
        ),
        expected_revision=1,
    )


@pytest.mark.contract
def test_registry_list_detail_filters_cursor_and_safe_projection(tmp_path):
    app, container = _manager(tmp_path)
    _add(
        container,
        agent_id="alpha",
        name="Alpha Lab",
        endpoint="https://10.0.0.11:8765",
        capabilities=("summary.v2", "logs.v2"),
    )
    _add(
        container,
        agent_id="beta",
        name="Beta Lab",
        endpoint="https://10.0.0.12:8765",
        enabled=False,
    )
    client = TestClient(app)

    missing = client.get("/api/v2/agents")
    first = client.get("/api/v2/agents?limit=1", headers=AUTH)
    cursor = first.json()["next_cursor"]
    second = client.get(f"/api/v2/agents?limit=1&cursor={cursor}", headers=AUTH)
    filtered = client.get(
        "/api/v2/agents?query=alpha&connection_status=ready&workload_status=healthy"
        "&capability=logs.v2",
        headers=AUTH,
    )
    detail = client.get("/api/v2/agents/alpha", headers=AUTH)

    assert missing.status_code == 401
    assert first.status_code == second.status_code == filtered.status_code == 200
    assert [item["agent_id"] for item in first.json()["agents"]] == ["alpha"]
    assert [item["agent_id"] for item in second.json()["agents"]] == ["beta"]
    assert [item["agent_id"] for item in filtered.json()["agents"]] == ["alpha"]
    assert detail.json()["agent"]["connection_status"] == "ready"
    serialized = first.text + second.text + detail.text
    for forbidden in (
        "credential_ref",
        "token-alpha",
        "Authorization",
        "token_file",
        "ssh_user",
        "private_key",
    ):
        assert forbidden not in serialized


@pytest.mark.contract
@pytest.mark.parametrize("limit", [0, 1001])
def test_registry_list_rejects_out_of_range_limit(tmp_path, limit):
    app, _container = _manager(tmp_path)
    response = TestClient(app).get(f"/api/v2/agents?limit={limit}", headers=AUTH)
    assert response.status_code == 422


@pytest.mark.contract
def test_registry_list_rejects_non_opaque_cursor(tmp_path):
    app, _container = _manager(tmp_path)
    response = TestClient(app).get("/api/v2/agents?cursor=alpha", headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_cursor"


@pytest.mark.contract
def test_enable_and_probe_are_audited_and_disabled_agents_are_not_dispatched(tmp_path):
    app, container = _manager(tmp_path)
    _add(
        container,
        agent_id="alpha",
        name="Alpha",
        endpoint="https://10.0.0.11:8765",
        enabled=False,
    )

    class Probe:
        calls = []

        async def probe(self, agent_id):
            self.calls.append(agent_id)
            return container.status_repository.get(agent_id)

    probe = Probe()
    app.dependency_overrides[get_fleet_probe_service] = lambda: probe
    client = TestClient(app)

    blocked = client.post("/api/v2/agents/alpha/probe", headers=AUTH)
    enabled = client.post(
        "/api/v2/agents/alpha/enabled", headers=AUTH, json={"enabled": True}
    )
    dispatched = client.post("/api/v2/agents/alpha/probe", headers=AUTH)

    assert blocked.status_code == 409
    assert enabled.status_code == dispatched.status_code == 200
    assert probe.calls == ["alpha"]
    events = client.get("/api/control-plane/audit?limit=10", headers=AUTH).json()["events"]
    assert {event["operation"] for event in events} >= {
        "agents.v2.enabled",
        "agents.v2.probe",
    }


@pytest.mark.contract
def test_audit_intent_failure_prevents_enable_mutation_and_probe_dispatch(tmp_path):
    app, container = _manager(tmp_path)
    _add(
        container,
        agent_id="alpha",
        name="Alpha",
        endpoint="https://10.0.0.11:8765",
        enabled=False,
    )

    class FailingAudit:
        def record_intent(self, _event):
            raise RuntimeError("audit unavailable")

    class Probe:
        calls = []

        async def probe(self, agent_id):
            self.calls.append(agent_id)

    probe = Probe()
    app.dependency_overrides[get_control_plane_audit_repository] = lambda: FailingAudit()
    app.dependency_overrides[get_fleet_probe_service] = lambda: probe
    client = TestClient(app, raise_server_exceptions=False)

    enable = client.post(
        "/api/v2/agents/alpha/enabled", headers=AUTH, json={"enabled": True}
    )
    dispatch = client.post("/api/v2/agents/alpha/probe", headers=AUTH)

    assert enable.status_code == dispatch.status_code == 503
    assert enable.json()["error"]["code"] == "audit_unavailable"
    assert container.registry_repository.get("alpha").enabled is False
    assert probe.calls == []
