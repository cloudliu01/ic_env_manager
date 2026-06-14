import pytest

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.registry import AgentRegistry
from ic_env_guard.config.models import AgentConfig


class FakeCapabilityClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def request(self, *_args, **_kwargs):
        return self

    def json(self):
        return self.payload


def _token_file(tmp_path):
    token_file = tmp_path / "agent-token"
    token_file.write_text("agent-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


def _agent(tmp_path):
    return AgentConfig(
        id="lab-01",
        name="Lab 01",
        base_url="https://lab-01.example",
        token_file=_token_file(tmp_path),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unsupported_agent_api_version_is_rejected(tmp_path):
    service = AgentAvailabilityService(
        AgentRegistry([_agent(tmp_path)]),
        FakeCapabilityClient(
            {
                "api_version": "0",
                "agent_version": "0.1.0",
                "capabilities": ["services.v1", "monitoring.snapshot.v1"],
            }
        ),
    )

    summary = await service.probe("lab-01")

    assert summary["status"] == "unavailable"
    assert summary["last_error"] == "agent_protocol_error"
    assert summary["capabilities"] == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_capabilities_degrade_agent_and_leave_features_disabled(tmp_path):
    service = AgentAvailabilityService(
        AgentRegistry([_agent(tmp_path)]),
        FakeCapabilityClient(
            {
                "api_version": "1",
                "agent_version": "0.2.0",
                "capabilities": ["services.v1"],
            }
        ),
    )

    summary = await service.probe("lab-01")

    assert summary["status"] == "degraded"
    assert summary["last_error"] == "missing_capabilities"
    assert summary["capabilities"] == ["services.v1"]
    assert "monitoring.snapshot.v1" not in summary["capabilities"]
