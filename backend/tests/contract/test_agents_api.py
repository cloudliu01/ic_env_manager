import httpx
import pytest
from fastapi.testclient import TestClient

from ic_env_guard.api.agent_http import get_agent_http_client
from ic_env_guard.config.models import AgentConfig, AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.main import create_app


def _token_file(tmp_path, name="token", value="secret-token"):
    token_file = tmp_path / name
    token_file.write_text(f"{value}\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


def _agent(tmp_path, agent_id="lab-01", enabled=True):
    return AgentConfig(
        id=agent_id,
        name=f"Agent {agent_id}",
        base_url=f"https://{agent_id}.example",
        token_file=_token_file(tmp_path, f"{agent_id}.token", "agent-secret-token"),
        enabled=enabled,
    )


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
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[_agent(tmp_path, "lab-01")],
    )
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
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
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[_agent(tmp_path, "lab-01")],
    )
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
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
