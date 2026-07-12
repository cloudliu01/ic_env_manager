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

CAPABILITIES = (
    "services.v1",
    "terminals.v1",
    "audit.v1",
    "monitoring.snapshot.v1",
)


def _token_file(tmp_path, name="token", value="secret-token"):
    token_file = tmp_path / name
    token_file.write_text(f"{value}\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


def _agent(tmp_path, agent_id):
    return AgentConfig(
        id=agent_id,
        name=agent_id,
        base_url=f"https://{agent_id}.example",
        token_file=_token_file(tmp_path, f"{agent_id}.token", "agent-secret-token"),
    )


def _ready_availability(config: AppConfig) -> AgentAvailabilityService:
    availability = AgentAvailabilityService(AgentRegistry(config.agents), AgentHttpClient())
    for agent_id in ("lab-01", "lab-02"):
        availability.record_ready_for_test(agent_id, datetime.now(UTC), capabilities=CAPABILITIES)
    return availability


@pytest.mark.integration
def test_overlapping_service_ids_stay_scoped_by_agent(tmp_path):
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        agent_name = request.url.host.split(".")[0]
        seen.append((request.url.host, request.url.path))
        return httpx.Response(
            200,
            json={
                "services": [
                    {
                        "id": "demo",
                        "name": f"Demo on {agent_name}",
                        "status": "configured",
                        "health_status": "unknown",
                        "allowed_operations": ["start", "stop"],
                    }
                ]
            },
        )

    config = AppConfig(
        auth=AuthConfig(token_file=_token_file(tmp_path)),
        mode="control-plane",
        control_plane=ControlPlaneConfig(audit_database=tmp_path / "control-plane.db"),
        agents=[_agent(tmp_path, "lab-01"), _agent(tmp_path, "lab-02")],
    )
    app = create_app(config=config)
    app.dependency_overrides[get_agent_http_client] = lambda: AgentHttpClient(
        transport=httpx.MockTransport(handler)
    )
    app.dependency_overrides[get_agent_availability] = lambda: _ready_availability(config)

    with TestClient(app) as client:
        first = client.get(
            "/api/agents/lab-01/services", headers={"Authorization": "Bearer secret-token"}
        )
        second = client.get(
            "/api/agents/lab-02/services", headers={"Authorization": "Bearer secret-token"}
        )

    assert first.json()["services"][0]["name"] == "Demo on lab-01"
    assert second.json()["services"][0]["name"] == "Demo on lab-02"
    assert seen == [
        ("lab-01.example", "/api/services"),
        ("lab-02.example", "/api/services"),
    ]
