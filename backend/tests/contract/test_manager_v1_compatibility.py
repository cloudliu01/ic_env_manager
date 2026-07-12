from collections.abc import Iterator
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

MANAGER_AUTH = {"Authorization": "Bearer manager-secret"}


def _token_file(tmp_path, name: str, token: str):
    token_file = tmp_path / name
    token_file.write_text(f"{token}\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


@pytest.fixture
def manager_client(tmp_path) -> Iterator[tuple[TestClient, list[tuple[str, str, str | None]]]]:
    manager_token = _token_file(tmp_path, "manager.token", "manager-secret")
    agent_token = _token_file(tmp_path, "lab-01.token", "agent-secret")
    config = AppConfig(
        auth=AuthConfig(token_file=manager_token),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[
            AgentConfig(
                id="lab-01",
                name="Lab 01",
                base_url="https://lab-01.example",
                token_file=agent_token,
            )
        ],
    )
    seen: list[tuple[str, str, str | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.url.path, request.headers.get("authorization")))
        return httpx.Response(
            200,
            json={
                "services": [
                    {
                        "id": "demo",
                        "name": "Demo service",
                        "status": "configured",
                        "health_status": "unknown",
                        "allowed_operations": ["start", "stop"],
                    }
                ]
            },
        )

    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    availability = AgentAvailabilityService(AgentRegistry(config.agents), AgentHttpClient())
    availability.record_ready_for_test("lab-01", datetime.now(UTC))
    app.dependency_overrides[get_agent_availability] = lambda: availability

    with TestClient(app) as client:
        yield client, seen


@pytest.mark.contract
def test_manager_v1_inventory_and_scoped_routes_remain_available(manager_client):
    client, seen = manager_client

    inventory = client.get("/api/agents", headers=MANAGER_AUTH)
    fleet = client.get("/api/fleet/overview", headers=MANAGER_AUTH)
    services = client.get("/api/agents/lab-01/services", headers=MANAGER_AUTH)

    assert inventory.status_code == 200
    assert fleet.status_code == 200
    assert services.status_code == 200
    assert inventory.json()["agents"][0]["id"] == "lab-01"
    assert fleet.json()["hosts"][0]["id"] == "lab-01"
    assert services.json()["services"][0]["id"] == "demo"
    for response in (inventory, fleet):
        assert "base_url" not in response.text
        assert "token_file" not in response.text
        assert "credential_ref" not in response.text
        assert "manager-secret" not in response.text
        assert "agent-secret" not in response.text
    assert seen == [("lab-01.example", "/api/services", "Bearer agent-secret")]


@pytest.mark.contract
@pytest.mark.parametrize(
    "path",
    ["/api/agents", "/api/fleet/overview", "/api/agents/lab-01/services"],
)
def test_manager_v1_routes_require_manager_auth(manager_client, path):
    client, seen = manager_client

    missing = client.get(path)
    agent_credential = client.get(path, headers={"Authorization": "Bearer agent-secret"})

    assert missing.status_code == 401
    assert agent_credential.status_code == 401
    assert seen == []
