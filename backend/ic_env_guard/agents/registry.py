from ic_env_guard.config.models import AgentConfig


class AgentNotFoundError(Exception):
    pass


class AgentRegistry:
    def __init__(self, agents: list[AgentConfig]) -> None:
        self._agents = {agent.id: agent for agent in agents}
        self._order = [agent.id for agent in agents]

    def get(self, agent_id: str) -> AgentConfig:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise AgentNotFoundError(agent_id) from exc

    def list_configs(self) -> list[AgentConfig]:
        return [self._agents[agent_id] for agent_id in self._order]

    def list_summaries(self) -> list[dict[str, object]]:
        return [self._summary(self._agents[agent_id]) for agent_id in self._order]

    def summary(self, agent_id: str) -> dict[str, object]:
        return self._summary(self.get(agent_id))

    def _summary(self, agent: AgentConfig) -> dict[str, object]:
        return {
            "id": agent.id,
            "name": agent.name,
            "enabled": agent.enabled,
            "status": "unknown" if agent.enabled else "disabled",
            "observed_at": None,
            "stale_after": None,
            "api_version": None,
            "agent_version": None,
            "capabilities": [],
            "last_error": None,
        }
