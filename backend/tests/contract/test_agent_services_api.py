from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentHttpClient
from ic_env_guard.agents.registry import AgentRegistry
from ic_env_guard.api.agent_http import get_agent_http_client
from ic_env_guard.api.agents import get_agent_availability
from ic_env_guard.api.audit_health import AuditStorageHealth, get_audit_storage_health
from ic_env_guard.api.control_plane_audit import get_control_plane_audit_repository
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
            allowed_agent_cidrs=["10.20.30.0/24"],
        ),
        agents=[
            AgentConfig(
                id="lab-01",
                name="Lab 01",
                base_url="https://10.20.30.1:8765",
                token_file=_token_file(tmp_path, "lab-01.token"),
            )
        ],
    )


def _ready_availability(config: AppConfig) -> AgentAvailabilityService:
    availability = AgentAvailabilityService(AgentRegistry(config.agents), AgentHttpClient())
    availability.record_ready_for_test(
        "lab-01", datetime.now(UTC), capabilities=tuple(CAPABILITIES["capabilities"])
    )
    return availability


class _CommitFailureSession:
    def __init__(self, *, fail_on_commit: int) -> None:
        self.commit_count = 0
        self.fail_on_commit = fail_on_commit
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1
        if self.commit_count == self.fail_on_commit:
            raise RuntimeError("audit commit failed")

    def rollback(self) -> None:
        self.rollback_count += 1


class _AuditRow:
    id = 1


class _CommitFailureAuditRepository:
    def __init__(self, session: _CommitFailureSession) -> None:
        self.session = session
        self.finalized = False

    def record_intent(self, event):  # type: ignore[no-untyped-def]
        return _AuditRow()

    def finalize(self, event_id, **kwargs):  # type: ignore[no-untyped-def]
        self.finalized = True
        return _AuditRow()


@pytest.mark.contract
def test_agent_service_list_dispatches_to_selected_agent(tmp_path):
    seen_paths = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        return httpx.Response(
            200,
            json={
                "services": [
                    {
                        "id": "demo",
                        "name": "Demo",
                        "status": "configured",
                        "health_status": "unknown",
                        "allowed_operations": ["start", "stop"],
                    }
                ]
            },
        )

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        response = client.get(
            "/api/agents/lab-01/services", headers={"Authorization": "Bearer secret-token"}
        )

    assert response.status_code == 200
    assert response.json()["services"][0]["id"] == "demo"
    assert seen_paths == ["/api/services"]


@pytest.mark.contract
def test_agent_service_mutation_preserves_upstream_status_and_audits(tmp_path):
    observed_headers = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        assert request.url.path == "/api/services/demo/start"
        observed_headers.update(request.headers)
        return httpx.Response(202, json={"operation_id": "op-1", "status": "accepted"})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        response = client.post(
            "/api/agents/lab-01/services/demo/start",
            headers={"Authorization": "Bearer secret-token"},
        )
        audit = client.get(
            "/api/control-plane/audit",
            headers={"Authorization": "Bearer secret-token"},
            params={"agent_id": "lab-01", "operation": "services.start"},
        )

    assert response.status_code == 202
    assert response.json()["operation_id"] == "op-1"
    event = audit.json()["events"][0]
    assert event["result"] == "success"
    assert event["correlation_id"]
    assert event["source_addr"]
    assert observed_headers["x-correlation-id"] == event["correlation_id"]


@pytest.mark.contract
def test_agent_service_intent_commit_failure_fails_closed_before_dispatch(tmp_path):
    dispatched = False

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched = True
        return httpx.Response(202, json={"operation_id": "op-1", "status": "accepted"})

    config = _config(tmp_path)
    audit_health = AuditStorageHealth()
    audit_repo = _CommitFailureAuditRepository(_CommitFailureSession(fail_on_commit=1))
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)
    app.dependency_overrides[get_audit_storage_health] = lambda: audit_health
    app.dependency_overrides[get_control_plane_audit_repository] = lambda: audit_repo

    with TestClient(app) as client:
        response = client.post(
            "/api/agents/lab-01/services/demo/start",
            headers={"Authorization": "Bearer secret-token"},
        )

    assert response.status_code == 503
    assert response.json()["error"] == "audit_storage_unavailable"
    assert dispatched is False
    assert audit_health.healthy is False
    assert audit_repo.session.rollback_count == 1


@pytest.mark.contract
def test_agent_service_outcome_commit_failure_preserves_upstream_response_and_degrades_readyz(
    tmp_path,
):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/services/demo/start"
        return httpx.Response(202, json={"operation_id": "op-1", "status": "accepted"})

    config = _config(tmp_path)
    audit_health = AuditStorageHealth()
    audit_repo = _CommitFailureAuditRepository(_CommitFailureSession(fail_on_commit=2))
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)
    app.dependency_overrides[get_audit_storage_health] = lambda: audit_health
    app.dependency_overrides[get_control_plane_audit_repository] = lambda: audit_repo

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.post("/api/agents/lab-01/services/demo/start", headers=headers)
        readyz = client.get("/readyz")

    assert response.status_code == 202
    assert response.json() == {"operation_id": "op-1", "status": "accepted"}
    assert audit_repo.finalized is True
    assert audit_repo.session.rollback_count == 1
    assert readyz.status_code == 503
    assert readyz.json()["audit_storage"] == "unavailable"


@pytest.mark.contract
def test_agent_service_mutation_timeout_is_reported_indeterminate_and_audited(tmp_path):
    async def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("agent timed out")

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.post("/api/agents/lab-01/services/demo/start", headers=headers)
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "services.start"},
        ).json()["events"]

    assert response.status_code == 424
    assert response.json()["error"] == "agent_operation_indeterminate"
    event = audit[0]
    assert event["result"] == "failed"
    assert event["dispatch_state"] == "unknown"
    assert event["failure_category"] == "agent_operation_indeterminate"


@pytest.mark.contract
def test_invalid_agent_service_action_is_audited_before_dispatch(tmp_path):
    dispatched = False

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched = True
        return httpx.Response(200, json={})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.post("/api/agents/lab-01/services/demo/reload", headers=headers)
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "operation": "services.reload"},
        ).json()["events"]

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_agent_request"
    assert dispatched is False
    event = audit[0]
    assert event["result"] == "failed"
    assert event["dispatch_state"] == "not_dispatched"
    assert event["failure_category"] == "invalid_agent_request"


@pytest.mark.contract
def test_agent_service_read_routes_dispatch_to_selected_agent_and_audit(tmp_path):
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        if request.url.path == "/api/services/demo/events":
            return httpx.Response(200, json={"events": []})
        if request.url.path == "/api/services/demo/logs":
            return httpx.Response(200, json={"lines": []})
        return httpx.Response(
            200,
            json={
                "id": "demo",
                "name": "Demo",
                "status": "running",
                "health_status": "healthy",
                "allowed_operations": ["start", "stop"],
            },
        )

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        detail = client.get("/api/agents/lab-01/services/demo", headers=headers)
        events = client.get("/api/agents/lab-01/services/demo/events", headers=headers)
        logs = client.get("/api/agents/lab-01/services/demo/logs", headers=headers)
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"agent_id": "lab-01", "limit": 10},
        ).json()["events"]

    assert detail.status_code == 200
    assert events.status_code == 200
    assert logs.status_code == 200
    assert seen == [
        ("GET", "/api/services/demo"),
        ("GET", "/api/services/demo/events"),
        ("GET", "/api/services/demo/logs"),
    ]
    operations = {event["operation"]: event for event in audit}
    assert operations["services.detail"]["correlation_id"]
    assert operations["services.events"]["correlation_id"]
    assert operations["services.logs"]["correlation_id"]


@pytest.mark.contract
def test_agent_service_upstream_error_body_includes_agent_and_correlation(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        return httpx.Response(404, json={"error": "service_not_found"})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        response = client.get(
            "/api/agents/lab-01/services/missing",
            headers={"Authorization": "Bearer secret-token"},
        )

    body = response.json()
    assert response.status_code == 404
    assert body["error"] == "service_not_found"
    assert body["agent_id"] == "lab-01"
    assert body["correlation_id"]


@pytest.mark.contract
def test_unknown_agent_service_route_rejects_without_dispatch(tmp_path):
    dispatched = False

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched = True
        return httpx.Response(200, json={})

    app = create_app(config=_config(tmp_path))
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/agents/missing/services", headers={"Authorization": "Bearer secret-token"}
        )

    assert response.status_code == 404
    assert response.json()["error"] == "agent_not_found"
    assert dispatched is False


@pytest.mark.contract
def test_unknown_agent_service_route_records_pre_dispatch_audit(tmp_path):
    app = create_app(config=_config(tmp_path))

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.get("/api/agents/missing/services", headers=headers)
        audit = client.get(
            "/api/control-plane/audit",
            headers=headers,
            params={"result": "failed"},
        ).json()["events"]

    assert response.status_code == 404
    event = audit[0]
    assert event["operation"] == "services.list"
    assert event["failure_category"] == "agent_not_found"
    assert event["dispatch_state"] == "not_dispatched"
    assert event["correlation_id"]


@pytest.mark.contract
def test_agent_service_route_rejects_missing_capability_before_dispatch(tmp_path):
    dispatched = False

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched = True
        return httpx.Response(200, json={})

    config = _config(tmp_path)
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = (
        lambda: app.state.container.agent_client.clone_with_transport(httpx.MockTransport(handler))
    )
    availability = AgentAvailabilityService(AgentRegistry(config.agents), AgentHttpClient())
    availability.record_ready_for_test("lab-01", datetime.now(UTC), capabilities=("terminals.v1",))
    app.dependency_overrides[get_agent_availability] = lambda: availability

    with TestClient(app) as client:
        headers = {"Authorization": "Bearer secret-token"}
        response = client.get("/api/agents/lab-01/services", headers=headers)
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
