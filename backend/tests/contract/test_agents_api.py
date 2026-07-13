from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from ic_env_guard.api.agent_http import get_agent_http_client
from ic_env_guard.api.agent_proxy import get_agent_http_proxy
from ic_env_guard.config.models import AgentConfig, AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.fleet.models import AgentRecord, EnrollmentMethod
from ic_env_guard.main import create_app


def _token_file(tmp_path, name="token", value="secret-token"):
    token_file = tmp_path / name
    token_file.write_text(f"{value}\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


def _agent(tmp_path, agent_id="lab-01", enabled=True, endpoint=None):
    return AgentConfig(
        id=agent_id,
        name=f"Agent {agent_id}",
        base_url=endpoint or f"https://{agent_id}.example",
        token_file=_token_file(tmp_path, f"{agent_id}.token", "agent-secret-token"),
        enabled=enabled,
    )


def _local_route_app(tmp_path, handler, *, gate=True, mutation=None, manager_target=False):
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
        credential_ref = container.credential_store.put(b"managed-route-token")
    now = datetime.now(UTC)
    container.registry_repository.create(
        AgentRecord(
            agent_id="local-agent",
            instance_id="11111111-1111-4111-8111-111111111111",
            display_name="Local Agent",
            normalized_endpoint=(
                "http://127.0.0.1:8765"
                if manager_target
                else "http://127.0.0.1:8766"
            ),
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
def test_local_agent_health_and_ready_use_managed_record_route(tmp_path, monkeypatch):
    dispatched = []

    async def handler(request: httpx.Request) -> httpx.Response:
        dispatched.append((request.url.path, request.headers.get("authorization")))
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(503, json={"status": "degraded"})

    app, credential_ref = _local_route_app(tmp_path, handler)
    reads = []
    original_read = app.state.container.credential_store.read

    def tracked_read(reference):
        reads.append(reference)
        return original_read(reference)

    monkeypatch.setattr(app.state.container.credential_store, "read", tracked_read)
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret-token"}
    health = client.get("/api/agents/local-agent/healthz", headers=headers)
    ready = client.get("/api/agents/local-agent/readyz", headers=headers)
    client.close()

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json() == {"status": "degraded"}
    assert reads == [credential_ref, credential_ref]
    assert dispatched == [
        ("/healthz", "Bearer managed-route-token"),
        ("/readyz", "Bearer managed-route-token"),
    ]


@pytest.mark.contract
@pytest.mark.parametrize("route", ["healthz", "readyz"])
@pytest.mark.parametrize(
    ("gate", "mutation"),
    [
        (False, None),
        (True, ("source", "manual")),
        (True, ("enrollment_method", "ssh_auto")),
        (True, ("transport_profile_id", "alternate-loopback-http")),
    ],
    ids=["gate-disabled", "source-mismatch", "method-mismatch", "profile-mismatch"],
)
def test_local_agent_status_route_rejects_invalid_authority_before_access(
    tmp_path, monkeypatch, route, gate, mutation
):
    dispatched = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        dispatched.append(True)
        return httpx.Response(200, json={"status": "ok"})

    app, _credential_ref = _local_route_app(
        tmp_path, handler, gate=gate, mutation=mutation
    )
    reads = []
    original_read = app.state.container.credential_store.read

    def tracked_read(reference):
        reads.append(reference)
        return original_read(reference)

    monkeypatch.setattr(app.state.container.credential_store, "read", tracked_read)
    client = TestClient(app)
    response = client.get(
        f"/api/agents/local-agent/{route}",
        headers={"Authorization": "Bearer secret-token"},
    )
    client.close()

    assert response.status_code == 409
    assert response.json()["error"] == "target_address_forbidden"
    assert reads == []
    assert dispatched == []


@pytest.mark.contract
def test_local_agent_status_route_rejects_manager_self_target_before_access(
    tmp_path, monkeypatch
):
    dispatched = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        dispatched.append(True)
        return httpx.Response(200, json={"status": "ok"})

    app, _credential_ref = _local_route_app(
        tmp_path, handler, manager_target=True
    )
    reads = []
    original_read = app.state.container.credential_store.read

    def tracked_read(reference):
        reads.append(reference)
        return original_read(reference)

    monkeypatch.setattr(app.state.container.credential_store, "read", tracked_read)
    client = TestClient(app)
    response = client.get(
        "/api/agents/local-agent/healthz",
        headers={"Authorization": "Bearer secret-token"},
    )
    client.close()

    assert response.status_code == 409
    assert response.json()["error"] == "target_is_manager"
    assert reads == []
    assert dispatched == []


@pytest.mark.contract
def test_agent_mode_exposes_local_capabilities(tmp_path):
    app = create_app(token_file=_token_file(tmp_path))

    with TestClient(app) as client:
        response = client.get("/api/capabilities", headers={"Authorization": "Bearer secret-token"})

    assert response.status_code == 200
    assert response.json()["api_version"] == "1"
    assert "services.v1" in response.json()["capabilities"]


@pytest.mark.contract
def test_app_factory_mounts_static_ui(tmp_path, monkeypatch):
    mounted = False

    def fake_mount_static_ui(_app):
        nonlocal mounted
        mounted = True

    monkeypatch.setattr("ic_env_guard.main.mount_static_ui", fake_mount_static_ui)

    create_app(token_file=_token_file(tmp_path))

    assert mounted is True


@pytest.mark.contract
def test_control_plane_inventory_returns_safe_agent_summaries(tmp_path):
    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[_agent(tmp_path, "lab-01"), _agent(tmp_path, "lab-02", enabled=False)],
    )

    with TestClient(create_app(config=config)) as client:
        response = client.get("/api/agents", headers={"Authorization": "Bearer secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert [agent["id"] for agent in body["agents"]] == ["lab-01", "lab-02"]
    assert body["agents"][0]["status"] == "unknown"
    assert body["agents"][1]["status"] == "disabled"
    assert "base_url" not in body["agents"][0]
    assert "token_file" not in body["agents"][0]
    assert "credential_ref" not in body["agents"][0]
    assert "secret-token" not in response.text


@pytest.mark.contract
def test_fleet_overview_returns_all_hosts_without_secrets(tmp_path):
    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[_agent(tmp_path, "lab-01"), _agent(tmp_path, "lab-02", enabled=False)],
    )

    with TestClient(create_app(config=config)) as client:
        response = client.get(
            "/api/fleet/overview", headers={"Authorization": "Bearer secret-token"}
        )

    assert response.status_code == 200
    body = response.json()
    assert [host["id"] for host in body["hosts"]] == ["lab-01", "lab-02"]
    assert body["collected_at"]
    assert body["hosts"][0]["status"] == "unknown"
    assert body["hosts"][1]["status"] == "disabled"
    assert "base_url" not in body["hosts"][0]
    assert "token_file" not in body["hosts"][0]
    assert "credential_ref" not in body["hosts"][0]
    assert "secret-token" not in response.text


@pytest.mark.contract
def test_fleet_overview_requires_auth(tmp_path):
    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[_agent(tmp_path, "lab-01")],
    )

    with TestClient(create_app(config=config)) as client:
        response = client.get("/api/fleet/overview")

    assert response.status_code == 401


@pytest.mark.contract
def test_control_plane_agent_detail_rejects_unknown_agent(tmp_path):
    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[_agent(tmp_path, "lab-01")],
    )

    with TestClient(create_app(config=config)) as client:
        response = client.get(
            "/api/agents/missing", headers={"Authorization": "Bearer secret-token"}
        )

    assert response.status_code == 404
    assert response.json()["error"] == "agent_not_found"


@pytest.mark.contract
def test_control_plane_probe_returns_agent_summary(tmp_path):
    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[_agent(tmp_path, "lab-01")],
    )

    with TestClient(create_app(config=config)) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.post(
            "/api/agents/lab-01/probe",
            headers=headers,
        )
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "agents.probe"},
        ).json()["events"]

    assert response.status_code == 200
    assert response.json()["id"] == "lab-01"
    assert audit[0]["result"] == "success"
    assert audit[0]["dispatch_state"] == "dispatched"
    assert audit[0]["source_addr"]
    assert audit[0]["correlation_id"]


@pytest.mark.contract
def test_control_plane_agent_health_and_ready_proxy_selected_agent(tmp_path):
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(503, json={"status": "degraded"})
        return httpx.Response(404, json={"error": "not_found"})

    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(
            audit_database=tmp_path / "control-plane.db",
            allowed_agent_cidrs=["10.20.30.0/24"],
        ),
        agents=[
            _agent(
                tmp_path, "lab-01", endpoint="https://10.20.30.1:8765"
            )
        ],
    )
    app = create_app(config=config)
    runtime_client = app.state.container.agent_client.clone_with_transport(
        httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_http_client] = lambda: runtime_client
    app.dependency_overrides[get_agent_http_proxy] = lambda: (
        app.state.container.agent_http_proxy.with_runtime(
            runtime_client, app.state.container.agent_availability
        )
    )

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        health = client.get("/api/agents/lab-01/healthz", headers=headers)
        ready = client.get("/api/agents/lab-01/readyz", headers=headers)
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "limit": 10},
        ).json()["events"]

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json() == {"status": "degraded"}
    assert seen == ["/healthz", "/readyz"]
    operations = {event["operation"]: event for event in audit}
    assert operations["agents.health"]["result"] == "success"
    assert operations["agents.health"]["upstream_status"] == 200
    assert operations["agents.health"]["source_addr"]
    assert operations["agents.ready"]["result"] == "success"
    assert operations["agents.ready"]["upstream_status"] == 503
    assert operations["agents.ready"]["source_addr"]


@pytest.mark.contract
def test_control_plane_agent_health_error_body_includes_agent_and_correlation(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(500, json={"error": "upstream_failed"})
        return httpx.Response(200, json={"status": "ready"})

    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(
            audit_database=tmp_path / "control-plane.db",
            allowed_agent_cidrs=["10.20.30.0/24"],
        ),
        agents=[
            _agent(
                tmp_path, "lab-01", endpoint="https://10.20.30.1:8765"
            )
        ],
    )
    app = create_app(config=config)
    runtime_client = app.state.container.agent_client.clone_with_transport(
        httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_http_client] = lambda: runtime_client
    app.dependency_overrides[get_agent_http_proxy] = lambda: (
        app.state.container.agent_http_proxy.with_runtime(
            runtime_client, app.state.container.agent_availability
        )
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/agents/lab-01/healthz",
            headers={"Authorization": "Bearer secret-token"},
        )

    body = response.json()
    assert response.status_code == 500
    assert body["error"] == "upstream_failed"
    assert body["agent_id"] == "lab-01"
    assert body["correlation_id"]


@pytest.mark.contract
def test_control_plane_agent_health_rejects_missing_and_disabled_before_dispatch(tmp_path):
    dispatched = False

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched = True
        return httpx.Response(200, json={"status": "ok"})

    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[_agent(tmp_path, "lab-01"), _agent(tmp_path, "disabled", enabled=False)],
    )
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
    )

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        missing = client.get("/api/agents/missing/readyz", headers=headers)
        disabled = client.get("/api/agents/disabled/readyz", headers=headers)
        probe_missing = client.post("/api/agents/missing/probe", headers=headers)
        health_disabled = client.get("/api/agents/disabled/healthz", headers=headers)
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"limit": 10, "result": "failed"},
        ).json()["events"]

    assert missing.status_code == 404
    assert missing.json()["error"] == "agent_not_found"
    assert disabled.status_code == 409
    assert disabled.json()["error"] == "agent_disabled"
    assert probe_missing.status_code == 404
    assert probe_missing.json()["error"] == "agent_not_found"
    assert health_disabled.status_code == 409
    assert health_disabled.json()["error"] == "agent_disabled"
    assert dispatched is False
    failures = {(event["operation"], event["failure_category"]): event for event in audit}
    assert failures[("agents.ready", "agent_not_found")]["dispatch_state"] == "not_dispatched"
    assert failures[("agents.ready", "agent_disabled")]["dispatch_state"] == "not_dispatched"
    assert failures[("agents.probe", "agent_not_found")]["dispatch_state"] == "not_dispatched"
    assert failures[("agents.health", "agent_disabled")]["dispatch_state"] == "not_dispatched"
    assert all(event["source_addr"] for event in failures.values())


@pytest.mark.contract
def test_agent_enabled_switch_disables_runtime_routing_and_audits(tmp_path):
    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[_agent(tmp_path, "lab-01")],
    )

    with TestClient(create_app(config=config)) as client:
        headers = {"Authorization": "Bearer secret-token"}
        disabled = client.post(
            "/api/agents/lab-01/enabled", headers=headers, json={"enabled": False}
        )
        probe = client.post("/api/agents/lab-01/probe", headers=headers)
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"operation": "agents.enabled"},
        ).json()["events"]

    assert disabled.status_code == 200
    assert disabled.json()["agent"]["status"] == "disabled"
    assert probe.status_code == 409
    assert probe.json()["error"] == "agent_disabled"
    assert audit[0]["result"] == "success"
    assert audit[0]["dispatch_state"] == "not_dispatched"


@pytest.mark.contract
def test_agent_enabled_switch_reenables_runtime_routing(tmp_path):
    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[_agent(tmp_path, "lab-01")],
    )

    with TestClient(create_app(config=config)) as client:
        headers = {"Authorization": "Bearer secret-token"}
        client.post("/api/agents/lab-01/enabled", headers=headers, json={"enabled": False})
        enabled = client.post("/api/agents/lab-01/enabled", headers=headers, json={"enabled": True})
        detail = client.get("/api/agents/lab-01", headers=headers)

    assert enabled.status_code == 200
    assert enabled.json()["agent"]["enabled"] is True
    assert detail.json()["enabled"] is True
    assert detail.json()["status"] == "unknown"


@pytest.mark.contract
def test_agent_enabled_switch_rejects_missing_agent_and_audits(tmp_path):
    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[_agent(tmp_path, "lab-01")],
    )

    with TestClient(create_app(config=config)) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.post(
            "/api/agents/missing/enabled", headers=headers, json={"enabled": False}
        )
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"operation": "agents.enabled", "result": "failed"},
        ).json()["events"]

    assert response.status_code == 404
    assert response.json()["error"] == "agent_not_found"
    assert audit[0]["failure_category"] == "agent_not_found"


@pytest.mark.contract
def test_agent_enabled_switch_rejects_invalid_enable_without_secret(tmp_path):
    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[
            AgentConfig(
                id="lab-01",
                name="Lab 01",
                base_url="https://lab-01.example",
                enabled=False,
            )
        ],
    )

    with TestClient(create_app(config=config)) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.post(
            "/api/agents/lab-01/enabled", headers=headers, json={"enabled": True}
        )
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"operation": "agents.enabled", "result": "failed"},
        ).json()["events"]

    assert response.status_code == 409
    assert response.json()["error"] == "agent_invalid_configuration"
    assert audit[0]["failure_category"] == "agent_invalid_configuration"
