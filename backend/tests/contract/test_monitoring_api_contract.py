from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentHttpClient
from ic_env_guard.agents.registry import AgentRegistry
from ic_env_guard.api.agent_http import get_agent_http_client
from ic_env_guard.api.agent_proxy import get_agent_http_proxy
from ic_env_guard.api.agents import get_agent_availability, get_agent_registry
from ic_env_guard.config.models import (
    AgentConfig,
    AppConfig,
    AuthConfig,
    ControlPlaneConfig,
)
from ic_env_guard.fleet.models import AgentRecord, AgentStatus, EnrollmentMethod
from ic_env_guard.main import create_app

DEPRECATED_MACHINE_SUCCESSOR = (
    '</api/agents/{agent_id}/monitoring/snapshot>; rel="successor-version"'
)

CAPABILITIES = {
    "api_version": "1",
    "agent_version": "0.2.0",
    "capabilities": ["services.v1", "terminals.v1", "audit.v1", "monitoring.snapshot.v1"],
}


@pytest.fixture
def client(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    return TestClient(create_app(token_file=token_file))


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer secret-token"}


def _token_file(tmp_path, name="token"):
    token_file = tmp_path / name
    token = "secret-token" if name == "token" else "agent-secret-token"
    token_file.write_text(f"{token}\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


def _control_plane_config(tmp_path):
    return AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[
            AgentConfig(
                id="lab-01",
                name="Lab 01",
                base_url="https://lab-01.example",
                token_file=_token_file(tmp_path, "lab-01.token"),
            ),
            AgentConfig(
                id="disabled",
                name="Disabled",
                base_url="https://disabled.example",
                enabled=False,
            ),
        ],
    )


def _ready_availability(config: AppConfig) -> AgentAvailabilityService:
    availability = AgentAvailabilityService(AgentRegistry(config.agents), AgentHttpClient())
    availability.record_ready_for_test(
        "lab-01", datetime.now(UTC), capabilities=tuple(CAPABILITIES["capabilities"])
    )
    return availability


def _local_monitoring_app(tmp_path, handler, *, gate=True, mutation=None):
    config = AppConfig.model_validate(
        {
            "mode": "control-plane",
            "auth": {"token_file": _token_file(tmp_path)},
            "development": {
                "allow_insecure_http": True,
                "local_agent_bootstrap": gate,
            },
            "enrollment": {"manager_socket_path": tmp_path / "manager.sock"},
            "control_plane": {
                "audit_database": tmp_path / "control-plane.db",
                "allowed_agent_cidrs": ["127.0.0.0/8"],
                "transport_profiles": [
                    {
                        "id": "local-loopback-http",
                        "type": "trusted_lan_http",
                        "allowed_cidrs": ["127.0.0.0/8"],
                    },
                    {
                        "id": "alternate-loopback-http",
                        "type": "trusted_lan_http",
                        "allowed_cidrs": ["127.0.0.0/8"],
                    },
                ],
            },
        }
    )
    app = create_app(config=config)
    container = app.state.container
    with container.credential_store.lifecycle_lease():
        credential_ref = container.credential_store.put(b"managed-monitoring-token")
    now = datetime.now(UTC)
    container.registry_repository.create(
        AgentRecord(
            agent_id="local-agent",
            instance_id="11111111-1111-4111-8111-111111111111",
            display_name="Local Agent",
            normalized_endpoint="http://127.0.0.1:8766",
            credential_ref=credential_ref,
            remote_credential_id="22222222-2222-4222-8222-222222222222",
            transport_profile_id="local-loopback-http",
            enrollment_method=EnrollmentMethod.LOCAL_SOCKET,
            enabled=True,
            source="local_dev_bootstrap",
            revision=1,
            created_at=now,
            updated_at=now,
        )
    )
    if mutation is not None:
        field, value = mutation
        with container.database_engine.begin() as connection:
            connection.exec_driver_sql(
                f"UPDATE agents SET {field}=? WHERE agent_id='local-agent'", (value,)
            )
    container.status_repository.update_if_target_revision(
        AgentStatus(
            agent_id="local-agent",
            target_revision=1,
            connection_status="ready",
            workload_status="healthy",
            observed_at=now,
            stale_after=now + timedelta(minutes=5),
            api_version="2",
            agent_version="0.2.0",
            capabilities=("monitoring.snapshot.v1",),
            summary={},
            last_error_code=None,
            updated_at=now,
        ),
        expected_revision=1,
    )
    runtime_client = container.agent_client.clone_with_transport(
        httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_http_client] = lambda: runtime_client
    app.dependency_overrides[get_agent_http_proxy] = lambda: (
        container.agent_http_proxy.with_runtime(
            runtime_client, container.agent_availability
        )
    )
    return app, credential_ref


@pytest.mark.contract
def test_local_agent_monitoring_uses_managed_record_route(tmp_path, monkeypatch):
    dispatched = []

    async def handler(request: httpx.Request) -> httpx.Response:
        dispatched.append(
            (request.url.path, request.headers.get("authorization"))
        )
        return httpx.Response(200, json=_snapshot("local-agent"))

    app, credential_ref = _local_monitoring_app(tmp_path, handler)
    reads = []
    original_read = app.state.container.credential_store.read

    def tracked_read(reference):
        reads.append(reference)
        return original_read(reference)

    monkeypatch.setattr(app.state.container.credential_store, "read", tracked_read)
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret-token"}
    response = client.get(
        "/api/agents/local-agent/monitoring/snapshot", headers=headers
    )
    audit = client.get(
        "/api/control-plane/audit",
        headers=headers,
        params={"agent_id": "local-agent", "operation": "monitoring.snapshot"},
    ).json()["events"]
    client.close()

    assert response.status_code == 200
    assert response.json()["host_id"] == "local-agent"
    assert reads == [credential_ref]
    assert dispatched == [
        ("/api/monitoring/local", "Bearer managed-monitoring-token")
    ]
    assert audit[0]["result"] == "success"
    assert audit[0]["dispatch_state"] == "dispatched"


@pytest.mark.contract
@pytest.mark.parametrize(
    "mutation",
    [
        ("source", "manual"),
        ("enrollment_method", "ssh_auto"),
        ("transport_profile_id", "alternate-loopback-http"),
    ],
    ids=["source-mismatch", "method-mismatch", "profile-mismatch"],
)
def test_local_monitoring_rejects_invalid_authority_before_access(
    tmp_path, monkeypatch, mutation
):
    dispatched = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        dispatched.append(True)
        return httpx.Response(200, json=_snapshot("local-agent"))

    app, _credential_ref = _local_monitoring_app(
        tmp_path, handler, mutation=mutation
    )
    reads = []
    original_read = app.state.container.credential_store.read

    def tracked_read(reference):
        reads.append(reference)
        return original_read(reference)

    monkeypatch.setattr(app.state.container.credential_store, "read", tracked_read)
    client = TestClient(app)
    response = client.get(
        "/api/agents/local-agent/monitoring/snapshot",
        headers={"Authorization": "Bearer secret-token"},
    )
    client.close()

    assert response.status_code == 409
    assert response.json()["error"] == "target_address_forbidden"
    assert reads == []
    assert dispatched == []


@pytest.mark.contract
def test_persisted_capability_cannot_bypass_disabled_local_gate(
    tmp_path, monkeypatch
):
    dispatched = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        dispatched.append(True)
        return httpx.Response(200, json=_snapshot("local-agent"))

    app, _credential_ref = _local_monitoring_app(tmp_path, handler, gate=False)
    reads = []
    original_read = app.state.container.credential_store.read

    def tracked_read(reference):
        reads.append(reference)
        return original_read(reference)

    monkeypatch.setattr(app.state.container.credential_store, "read", tracked_read)
    client = TestClient(app)
    response = client.get(
        "/api/agents/local-agent/monitoring/snapshot",
        headers={"Authorization": "Bearer secret-token"},
    )
    client.close()

    assert response.status_code == 409
    assert response.json()["error"] == "target_address_forbidden"
    assert reads == []
    assert dispatched == []


@pytest.mark.contract
def test_persisted_legacy_config_triple_uses_direct_monitoring_route(tmp_path):
    dispatched = []

    async def handler(request: httpx.Request) -> httpx.Response:
        dispatched.append(request.url.path)
        return httpx.Response(200, json=_snapshot("local-agent"))

    app, _credential_ref = _local_monitoring_app(tmp_path, handler)
    with app.state.container.database_engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE agents SET source='config_import', "
            "enrollment_method='legacy_admin_token', "
            "transport_profile_id='legacy-config-http' "
            "WHERE agent_id='local-agent'"
        )
    client = TestClient(app)
    response = client.get(
        "/api/agents/local-agent/monitoring/snapshot",
        headers={"Authorization": "Bearer secret-token"},
    )
    client.close()

    assert response.status_code == 200
    assert response.json()["host_id"] == "local-agent"
    assert dispatched == ["/api/monitoring/local"]


@pytest.mark.contract
def test_forged_legacy_markers_with_managed_profile_stay_on_monitoring_proxy(
    tmp_path, monkeypatch
):
    dispatched = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        dispatched.append(True)
        return httpx.Response(200, json=_snapshot("local-agent"))

    app, _credential_ref = _local_monitoring_app(tmp_path, handler)
    with app.state.container.database_engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE agents SET source='config_import', "
            "enrollment_method='legacy_admin_token', "
            "transport_profile_id='alternate-loopback-http' "
            "WHERE agent_id='local-agent'"
        )
    reads = []
    original_read = app.state.container.credential_store.read

    def tracked_read(reference):
        reads.append(reference)
        return original_read(reference)

    monkeypatch.setattr(app.state.container.credential_store, "read", tracked_read)
    client = TestClient(app)
    response = client.get(
        "/api/agents/local-agent/monitoring/snapshot",
        headers={"Authorization": "Bearer secret-token"},
    )
    client.close()

    assert response.status_code == 409
    assert response.json()["error"] == "target_address_forbidden"
    assert reads == []
    assert dispatched == []


def _snapshot(host_id="lab-01"):
    return {
        "host_id": host_id,
        "name": host_id,
        "address": f"{host_id}.example",
        "hostname": host_id,
        "status": "online",
        "sampled_at": "2026-06-14T00:00:00Z",
        "cpu": {
            "percent": 12.5,
            "cores_logical": 8,
            "cores_physical": 4,
            "load_average": [1.0, 1.1, 1.2],
        },
        "memory": {"used_bytes": 1, "total_bytes": 4, "percent": 25},
        "swap": {"used_bytes": 0, "total_bytes": 0, "percent": 0},
        "disks": [{"mount": "/", "used_bytes": 1, "total_bytes": 4, "percent": 25}],
        "network": [{"interface": "eth0", "rx_bytes": 1, "tx_bytes": 2}],
        "uptime_seconds": 3600,
    }


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
def test_local_monitoring_snapshot_survives_swap_memory_oserror(client, auth_headers, monkeypatch):
    def unavailable_swap_memory():
        raise OSError

    monkeypatch.setattr(
        "ic_env_guard.monitoring.snapshot.psutil.swap_memory", unavailable_swap_memory
    )

    response = client.get("/api/monitoring/local", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["swap"] == {
        "used_bytes": 0,
        "total_bytes": 0,
        "free_bytes": 0,
        "percent": 0,
    }


@pytest.mark.contract
def test_machine_crud_does_not_return_key(client, auth_headers):
    create = client.post(
        "/api/monitoring/machines",
        headers=auth_headers,
        json={"name": "Lab host", "address": "127.0.0.1", "port": 8765, "key": "remote-key"},
    )

    assert create.status_code == 201
    assert create.headers["Deprecation"] == "true"
    assert create.headers["Link"] == DEPRECATED_MACHINE_SUCCESSOR
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
    assert delete.headers["Deprecation"] == "true"
    assert delete.headers["Link"] == DEPRECATED_MACHINE_SUCCESSOR
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


@pytest.mark.contract
def test_agent_monitoring_snapshot_dispatches_to_selected_agent(tmp_path):
    seen_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        return httpx.Response(200, json=_snapshot("lab-01"))

    config = _control_plane_config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_registry] = lambda: AgentRegistry(config.agents)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(
            httpx.MockTransport(handler)
        )
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.get(
            "/api/agents/lab-01/monitoring/snapshot",
            headers=headers,
        )
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "monitoring.snapshot"},
        )

    assert response.status_code == 200
    assert response.json()["host_id"] == "lab-01"
    assert seen_paths == ["/api/monitoring/local"]
    event = audit.json()["events"][0]
    assert event["result"] == "success"
    assert event["dispatch_state"] == "dispatched"
    assert event["correlation_id"]


@pytest.mark.contract
def test_agent_monitoring_snapshot_preserves_upstream_status(tmp_path):
    async def capability_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        return httpx.Response(503, json={"status": "offline", "error": "agent degraded"})

    config = _control_plane_config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_registry] = lambda: AgentRegistry(config.agents)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(
            httpx.MockTransport(capability_handler)
        )
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        response = client.get(
            "/api/agents/lab-01/monitoring/snapshot",
            headers={"Authorization": "Bearer secret-token"},
        )

    assert response.status_code == 503
    assert response.json()["error"] == "agent degraded"
    assert response.json()["agent_id"] == "lab-01"
    assert response.json()["correlation_id"]


@pytest.mark.contract
def test_legacy_agent_monitoring_timeout_preserves_error_and_audit(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("agent timed out", request=request)

    config = _control_plane_config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_registry] = lambda: AgentRegistry(config.agents)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(
            httpx.MockTransport(handler)
        )
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.get(
            "/api/agents/lab-01/monitoring/snapshot", headers=headers
        )
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "monitoring.snapshot"},
        ).json()["events"]

    assert response.status_code == 504
    assert response.json()["error"] == "agent_timeout"
    assert audit[0]["result"] == "failed"
    assert audit[0]["failure_category"] == "agent_timeout"
    assert audit[0]["dispatch_state"] == "unknown"


@pytest.mark.contract
def test_agent_monitoring_snapshot_rejects_before_dispatch(tmp_path):
    dispatched = False

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched = True
        return httpx.Response(200, json=_snapshot())

    app = create_app(config=_control_plane_config(tmp_path))
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
    )

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        missing = client.get("/api/agents/missing/monitoring/snapshot", headers=headers)
        disabled = client.get("/api/agents/disabled/monitoring/snapshot", headers=headers)

    assert missing.status_code == 404
    assert missing.json()["error"] == "agent_not_found"
    assert disabled.status_code == 409
    assert disabled.json()["error"] == "agent_disabled"
    assert dispatched is False


@pytest.mark.contract
def test_agent_monitoring_snapshot_rejects_missing_capability_before_dispatch(tmp_path):
    dispatched = False

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched = True
        return httpx.Response(200, json=_snapshot())

    config = _control_plane_config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
    )
    availability = AgentAvailabilityService(AgentRegistry(config.agents), AgentHttpClient())
    availability.record_ready_for_test("lab-01", datetime.now(UTC), capabilities=("services.v1",))
    app.dependency_overrides[get_agent_availability] = lambda: availability

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.get("/api/agents/lab-01/monitoring/snapshot", headers=headers)
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"result": "failed"},
        ).json()["events"]

    assert response.status_code == 409
    assert response.json()["error"] == "agent_capability_missing"
    assert dispatched is False
    assert audit[0]["failure_category"] == "missing_capability"
    assert audit[0]["dispatch_state"] == "not_dispatched"
