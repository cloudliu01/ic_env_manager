from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentHttpClient
from ic_env_guard.agents.registry import AgentRegistry
from ic_env_guard.api.agent_http import get_agent_http_client
from ic_env_guard.api.agents import get_agent_availability
from ic_env_guard.config.models import AgentConfig, AppConfig, AuthConfig, ControlPlaneConfig
from ic_env_guard.main import create_app

CAPABILITIES = {
    "api_version": "1",
    "agent_version": "0.2.0",
    "capabilities": ["services.v1", "terminals.v1", "audit.v1", "monitoring.snapshot.v1"],
}


def _token_file(tmp_path, name="token"):
    token_file = tmp_path / name
    token = "secret-token" if name == "token" else "agent-secret-token"
    token_file.write_text(f"{token}\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


def _agent(tmp_path, agent_id: str, *, enabled: bool = True) -> AgentConfig:
    return AgentConfig(
        id=agent_id,
        name=agent_id,
        base_url=f"https://{agent_id}.example",
        token_file=_token_file(tmp_path, f"{agent_id}.token") if enabled else None,
        enabled=enabled,
    )


def _config(tmp_path) -> AppConfig:
    return AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[
            _agent(tmp_path, "lab-01"),
            _agent(tmp_path, "lab-02"),
            _agent(tmp_path, "disabled", enabled=False),
        ],
    )


def _audit_event(operation: str, target_id: str) -> dict[str, object]:
    return {
        "id": 1,
        "timestamp": "2026-06-14T00:00:00Z",
        "actor_id": "local-admin",
        "source_addr": "127.0.0.1",
        "operation": operation,
        "target_type": "service",
        "target_id": target_id,
        "result": "success",
        "failure_reason": None,
    }


def _ready_availability(config: AppConfig) -> AgentAvailabilityService:
    availability = AgentAvailabilityService(AgentRegistry(config.agents), AgentHttpClient())
    for agent_id in ("lab-01", "lab-02"):
        availability.record_ready_for_test(
            agent_id, datetime.now(UTC), capabilities=tuple(CAPABILITIES["capabilities"])
        )
    return availability


@pytest.mark.integration
def test_agent_audit_route_forwards_filters_and_stamps_agent_id(tmp_path):
    seen: list[tuple[str, dict[str, str]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        seen.append((request.url.path, dict(request.url.params.multi_items())))
        return httpx.Response(200, json={"events": [_audit_event("service.start", "demo")]})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.get(
            "/api/agents/lab-01/audit",
            headers=headers,
            params={"limit": 2, "target_type": "service", "result": "success"},
        )
        gateway_audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "audit.list"},
        )

    assert response.status_code == 200
    assert seen == [
        ("/api/audit", {"limit": "2", "target_type": "service", "result": "success"})
    ]
    assert response.json()["events"] == [
        {**_audit_event("service.start", "demo"), "agent_id": "lab-01"}
    ]
    event = gateway_audit.json()["events"][0]
    assert event["result"] == "success"
    assert event["dispatch_state"] == "dispatched"
    assert event["correlation_id"]


@pytest.mark.integration
def test_agent_audit_route_keeps_agent_results_isolated(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        agent_id = request.url.host.split(".")[0]
        return httpx.Response(
            200,
            json={"events": [_audit_event("service.stop", f"demo-{agent_id}")]},
        )

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        first = client.get("/api/agents/lab-01/audit", headers=headers)
        second = client.get("/api/agents/lab-02/audit", headers=headers)

    assert first.json()["events"][0]["target_id"] == "demo-lab-01"
    assert first.json()["events"][0]["agent_id"] == "lab-01"
    assert second.json()["events"][0]["target_id"] == "demo-lab-02"
    assert second.json()["events"][0]["agent_id"] == "lab-02"


@pytest.mark.integration
def test_agent_audit_upstream_error_body_includes_agent_and_correlation(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        return httpx.Response(400, json={"error": "invalid_filter"})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        response = client.get(
            "/api/agents/lab-01/audit",
            headers={"Authorization": "Bearer secret-token"},
        )

    body = response.json()
    assert response.status_code == 400
    assert body["error"] == "invalid_filter"
    assert body["agent_id"] == "lab-01"
    assert body["correlation_id"]


@pytest.mark.integration
def test_agent_audit_route_rejects_unknown_and_disabled_before_dispatch(tmp_path):
    dispatched = False

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched = True
        return httpx.Response(200, json={"events": []})

    app = create_app(config=_config(tmp_path))
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        missing = client.get("/api/agents/missing/audit", headers=headers)
        disabled = client.get("/api/agents/disabled/audit", headers=headers)

    assert missing.status_code == 404
    assert missing.json()["error"] == "agent_not_found"
    assert disabled.status_code == 409
    assert disabled.json()["error"] == "agent_disabled"
    assert dispatched is False


@pytest.mark.integration
def test_agent_audit_route_validates_limit(tmp_path):
    app = create_app(config=_config(tmp_path))

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        too_low = client.get("/api/agents/lab-01/audit?limit=0", headers=headers)
        too_high = client.get("/api/agents/lab-01/audit?limit=1001", headers=headers)

    assert too_low.status_code == 422
    assert too_high.status_code == 422


@pytest.mark.integration
def test_agent_audit_route_rejects_missing_capability_before_dispatch(tmp_path):
    dispatched = False

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched = True
        return httpx.Response(200, json={"events": []})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    availability = AgentAvailabilityService(AgentRegistry(config.agents), AgentHttpClient())
    availability.record_ready_for_test(
        "lab-01", datetime.now(UTC), capabilities=("services.v1",)
    )
    app.dependency_overrides[get_agent_availability] = lambda: availability

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.get("/api/agents/lab-01/audit", headers=headers)
        gateway_audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"result": "failed"},
        ).json()["events"]

    assert response.status_code == 409
    assert response.json()["error"] == "agent_capability_missing"
    assert dispatched is False
    assert gateway_audit[0]["failure_category"] == "missing_capability"
    assert gateway_audit[0]["dispatch_state"] == "not_dispatched"
