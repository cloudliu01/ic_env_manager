import pytest

from ic_env_guard.agents.registry import AgentNotFoundError, AgentRegistry
from ic_env_guard.config.models import AgentConfig


def _token_file(tmp_path):
    token_file = tmp_path / "agent-token"
    token_file.write_text("agent-secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


def _agent(tmp_path, agent_id="lab-01", enabled=True):
    return AgentConfig(
        id=agent_id,
        name=f"Agent {agent_id}",
        base_url=f"https://{agent_id}.example",
        token_file=_token_file(tmp_path),
        enabled=enabled,
    )


@pytest.mark.unit
def test_agent_registry_resolves_configured_agents(tmp_path):
    registry = AgentRegistry([_agent(tmp_path, "lab-01")])

    assert registry.get("lab-01").name == "Agent lab-01"


@pytest.mark.unit
def test_agent_registry_rejects_unknown_agents(tmp_path):
    registry = AgentRegistry([_agent(tmp_path, "lab-01")])

    with pytest.raises(AgentNotFoundError):
        registry.get("missing")


@pytest.mark.unit
def test_agent_registry_returns_safe_immutable_summaries(tmp_path):
    registry = AgentRegistry(
        [_agent(tmp_path, "lab-01"), _agent(tmp_path, "lab-02", enabled=False)]
    )

    summaries = registry.list_summaries()
    summaries.append({"id": "mutated"})

    fresh = registry.list_summaries()
    assert [summary["id"] for summary in fresh] == ["lab-01", "lab-02"]
    assert fresh[0]["status"] == "unknown"
    assert fresh[1]["status"] == "disabled"
    assert "base_url" not in fresh[0]
    assert "token_file" not in fresh[0]
