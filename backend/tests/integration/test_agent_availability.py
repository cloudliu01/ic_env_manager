from datetime import UTC, datetime, timedelta

import pytest

from ic_env_guard.agents.availability import AgentAvailabilityService
from ic_env_guard.agents.client import AgentClientError
from ic_env_guard.agents.models import LOCAL_CAPABILITIES
from ic_env_guard.agents.registry import AgentRegistry
from ic_env_guard.config.models import AgentConfig


class FakeClient:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload or {
            "api_version": "1",
            "agent_version": "0.2.0",
            "capabilities": LOCAL_CAPABILITIES,
        }
        self.error = error

    async def request(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        return self

    def json(self):
        return self.payload


def _token_file(tmp_path):
    token_file = tmp_path / "agent-token"
    token_file.write_text("agent-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


def _agent(tmp_path, enabled=True):
    return AgentConfig(
        id="lab-01",
        name="Lab 01",
        base_url="https://lab-01.example",
        token_file=_token_file(tmp_path),
        enabled=enabled,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_availability_probe_records_ready_status(tmp_path):
    service = AgentAvailabilityService(AgentRegistry([_agent(tmp_path)]), FakeClient())

    summary = await service.probe("lab-01")

    assert summary["status"] == "ready"
    assert summary["api_version"] == "1"
    assert summary["capabilities"] == LOCAL_CAPABILITIES


@pytest.mark.integration
@pytest.mark.asyncio
async def test_disabled_agent_status_does_not_probe(tmp_path):
    service = AgentAvailabilityService(
        AgentRegistry([_agent(tmp_path, enabled=False)]), FakeClient()
    )

    summary = await service.probe("lab-01")

    assert summary["status"] == "disabled"
    assert summary["capabilities"] == []


@pytest.mark.integration
def test_stale_observation_returns_unknown(tmp_path):
    service = AgentAvailabilityService(
        AgentRegistry([_agent(tmp_path)]), FakeClient(), stale_after_seconds=1
    )
    service.record_ready_for_test(
        "lab-01", observed_at=datetime.now(UTC) - timedelta(seconds=5)
    )

    assert service.summary("lab-01")["status"] == "unknown"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_protocol_failure_marks_agent_unavailable(tmp_path):
    service = AgentAvailabilityService(
        AgentRegistry([_agent(tmp_path)]),
        FakeClient(error=AgentClientError("agent_protocol_error", "missing capabilities")),
    )

    summary = await service.probe("lab-01")

    assert summary["status"] == "unavailable"
    assert summary["last_error"] == "agent_protocol_error"


@pytest.mark.integration
def test_runtime_disable_clears_ready_observation(tmp_path):
    registry = AgentRegistry([_agent(tmp_path)])
    service = AgentAvailabilityService(registry, FakeClient())
    service.record_ready_for_test("lab-01", observed_at=datetime.now(UTC))

    registry.set_enabled("lab-01", False)
    service.clear("lab-01")

    assert service.summary("lab-01")["status"] == "disabled"
    assert service.summary("lab-01")["capabilities"] == []

    registry.set_enabled("lab-01", True)

    assert service.summary("lab-01")["status"] == "unknown"
