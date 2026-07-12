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


def _config(tmp_path):
    return AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(
            audit_database=tmp_path / "control-plane.db",
            max_outstanding_tickets=1,
        ),
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
            )
        ],
    )


def _ready_availability(config: AppConfig) -> AgentAvailabilityService:
    availability = AgentAvailabilityService(AgentRegistry(config.agents), AgentHttpClient())
    availability.record_ready_for_test(
        "lab-01", datetime.now(UTC), capabilities=tuple(CAPABILITIES["capabilities"])
    )
    return availability


@pytest.mark.contract
def test_agent_terminal_http_routes_dispatch_to_selected_agent(tmp_path):
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        if request.method == "POST" and request.url.path == "/api/terminals":
            return httpx.Response(
                201,
                json={
                    "id": "term-1",
                    "title": "demo",
                    "status": "running",
                    "output_cursor": 0,
                },
            )
        if request.url.path == "/api/terminals/term-1/history":
            return httpx.Response(
                200,
                json={
                    "terminal_id": "term-1",
                    "from_cursor": 0,
                    "to_cursor": 0,
                    "buffer_start_cursor": 0,
                    "truncated": False,
                    "status": "running",
                    "output": "",
                },
            )
        if request.url.path == "/api/terminals/term-1/resize":
            return httpx.Response(204)
        if request.url.path == "/api/terminals/term-1":
            return httpx.Response(202, json={"id": "term-1", "status": "closed"})
        return httpx.Response(200, json={"terminals": [{"id": "term-1"}]})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        list_response = client.get("/api/agents/lab-01/terminals", headers=headers)
        create_response = client.post("/api/agents/lab-01/terminals", headers=headers, json={})
        detail_response = client.get("/api/agents/lab-01/terminals/term-1", headers=headers)
        assert client.get(
            "/api/agents/lab-01/terminals/term-1/history?cursor=0", headers=headers
        ).status_code == 200
        assert client.post(
            "/api/agents/lab-01/terminals/term-1/resize",
            headers=headers,
            json={"rows": 30, "cols": 100},
        ).status_code == 204
        close_response = client.delete("/api/agents/lab-01/terminals/term-1", headers=headers)

    assert list_response.status_code == 200
    assert create_response.status_code == 201
    assert detail_response.status_code == 202
    assert close_response.status_code == 202

    assert ("GET", "/api/terminals") in seen
    assert ("POST", "/api/terminals") in seen
    assert ("GET", "/api/terminals/term-1") in seen
    assert ("GET", "/api/terminals/term-1/history") in seen
    assert ("POST", "/api/terminals/term-1/resize") in seen
    assert ("DELETE", "/api/terminals/term-1") in seen


@pytest.mark.contract
def test_gateway_connect_token_capacity_fails_before_upstream_dispatch(tmp_path):
    dispatched_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        dispatched_paths.append(request.url.path)
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        return httpx.Response(201, json={"ticket": "upstream-ticket", "expires_in_seconds": 60})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        first = client.post(
            "/api/agents/lab-01/terminals/term-1/connect-token", headers=headers
        )
        second = client.post(
            "/api/agents/lab-01/terminals/term-2/connect-token", headers=headers
        )

    assert first.status_code == 201
    assert first.json()["ticket"] != "upstream-ticket"
    assert second.status_code == 429
    assert second.json()["error"] == "gateway_capacity_exceeded"
    assert dispatched_paths == ["/api/terminals/term-1/connect-token"]


@pytest.mark.contract
def test_gateway_connect_token_preserves_upstream_error_and_releases_capacity(tmp_path):
    dispatched_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        dispatched_paths.append(request.url.path)
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        if len(dispatched_paths) == 1:
            return httpx.Response(404, json={"error": "terminal_not_found"})
        return httpx.Response(201, json={"ticket": "upstream-ticket", "expires_in_seconds": 60})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        failed = client.post(
            "/api/agents/lab-01/terminals/missing/connect-token", headers=headers
        )
        retry = client.post(
            "/api/agents/lab-01/terminals/term-1/connect-token", headers=headers
        )

    assert failed.status_code == 404
    assert failed.json()["error"] == "terminal_not_found"
    assert failed.json()["agent_id"] == "lab-01"
    assert failed.json()["correlation_id"]
    assert retry.status_code == 201
    assert retry.json()["ticket"] != "upstream-ticket"
    assert dispatched_paths == [
        "/api/terminals/missing/connect-token",
        "/api/terminals/term-1/connect-token",
    ]


@pytest.mark.contract
def test_gateway_connect_token_rejects_malformed_upstream_payload_and_audits(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        return httpx.Response(201, json={"expires_in_seconds": 60})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.post(
            "/api/agents/lab-01/terminals/term-1/connect-token", headers=headers
        )
        retry = client.post(
            "/api/agents/lab-01/terminals/term-1/connect-token", headers=headers
        )
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "terminals.connect-token"},
        ).json()["events"]

    assert response.status_code == 502
    assert response.json()["error"] == "agent_protocol_error"
    assert retry.status_code == 502
    latest = audit[0]
    assert latest["result"] == "failed"
    assert latest["dispatch_state"] == "dispatched"
    assert latest["upstream_status"] == 201
    assert latest["failure_category"] == "agent_protocol_error"


@pytest.mark.contract
def test_agent_terminal_upstream_error_body_includes_agent_and_correlation(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        return httpx.Response(404, json={"error": "terminal_not_found"})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        response = client.get(
            "/api/agents/lab-01/terminals/missing",
            headers={"Authorization": "Bearer secret-token"},
        )

    body = response.json()
    assert response.status_code == 404
    assert body["error"] == "terminal_not_found"
    assert body["agent_id"] == "lab-01"
    assert body["correlation_id"]


@pytest.mark.contract
def test_agent_terminal_mutations_record_gateway_audit(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        if request.url.path == "/api/terminals":
            return httpx.Response(
                201,
                json={"id": "term-1", "title": "demo", "status": "running", "output_cursor": 0},
            )
        if request.url.path == "/api/terminals/term-1/resize":
            return httpx.Response(204)
        if request.url.path == "/api/terminals/term-1/connect-token":
            return httpx.Response(201, json={"ticket": "upstream-ticket", "expires_in_seconds": 60})
        if request.url.path == "/api/terminals/term-1":
            return httpx.Response(202, json={"id": "term-1", "status": "closed"})
        return httpx.Response(404, json={"error": "not_found"})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        assert (
            client.post("/api/agents/lab-01/terminals", headers=headers, json={}).status_code
            == 201
        )
        assert client.post(
            "/api/agents/lab-01/terminals/term-1/resize",
            headers=headers,
            json={"rows": 30, "cols": 100},
        ).status_code == 204
        assert client.post(
            "/api/agents/lab-01/terminals/term-1/connect-token", headers=headers
        ).status_code == 201
        assert (
            client.delete("/api/agents/lab-01/terminals/term-1", headers=headers).status_code
            == 202
        )

        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "limit": 10},
        ).json()["events"]

    operations = {event["operation"]: event for event in audit}
    assert operations["terminals.create"]["result"] == "success"
    assert operations["terminals.resize"]["result"] == "success"
    assert operations["terminals.connect-token"]["result"] == "success"
    assert operations["terminals.close"]["result"] == "success"
    assert all(event["correlation_id"] for event in operations.values())


@pytest.mark.contract
def test_agent_terminal_pre_dispatch_failures_are_audited(tmp_path):
    dispatched = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        dispatched = True
        return httpx.Response(201, json={"ticket": "upstream-ticket", "expires_in_seconds": 60})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        missing = client.post("/api/agents/missing/terminals", headers=headers, json={})
        disabled = client.post("/api/agents/disabled/terminals", headers=headers, json={})
        first = client.post("/api/agents/lab-01/terminals/term-1/connect-token", headers=headers)
        capacity = client.post("/api/agents/lab-01/terminals/term-2/connect-token", headers=headers)
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"limit": 10, "result": "failed"},
        ).json()["events"]

    assert missing.status_code == 404
    assert disabled.status_code == 409
    assert first.status_code == 201
    assert capacity.status_code == 429
    assert dispatched is True
    failures = {event["failure_category"]: event for event in audit}
    assert failures["agent_not_found"]["dispatch_state"] == "not_dispatched"
    assert failures["agent_disabled"]["dispatch_state"] == "not_dispatched"
    assert failures["gateway_capacity_exceeded"]["dispatch_state"] == "not_dispatched"


@pytest.mark.contract
def test_agent_terminal_route_rejects_missing_capability_before_dispatch(tmp_path):
    dispatched = False

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched = True
        return httpx.Response(200, json={})

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
        response = client.get("/api/agents/lab-01/terminals", headers=headers)
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
