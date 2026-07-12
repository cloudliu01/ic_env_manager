from ic_env_guard.config.models import AgentConfig
from ic_env_guard.fleet.registry import (
    FleetRegistry,
    FleetRegistryConfigurationError,
    FleetRegistryConflict,
)


class AgentNotFoundError(Exception):
    pass


class AgentInvalidConfigurationError(Exception):
    pass


class AgentRegistry:
    def __init__(self, agents: list[AgentConfig] | FleetRegistry) -> None:
        self._fleet = agents if isinstance(agents, FleetRegistry) else None
        configured = [] if self._fleet is not None else agents
        self._agents = {agent.id: agent for agent in configured}
        self._order = [agent.id for agent in configured]

    def get(self, agent_id: str) -> AgentConfig:
        if self._fleet is not None:
            agent = self._fleet.get(agent_id)
            if agent is None:
                raise AgentNotFoundError(agent_id)
            return agent
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise AgentNotFoundError(agent_id) from exc

    def list_configs(self) -> list[AgentConfig]:
        if self._fleet is not None:
            return self._fleet.list()
        return [self._agents[agent_id] for agent_id in self._order]

    def revision(self, agent_id: str) -> int | None:
        if self._fleet is None:
            return None
        record = self._fleet.record(agent_id)
        return record.revision if record is not None else None

    def list_summaries(self) -> list[dict[str, object]]:
        return [self._summary(agent) for agent in self.list_configs()]

    def summary(self, agent_id: str) -> dict[str, object]:
        return self._summary(self.get(agent_id))

    def set_enabled(self, agent_id: str, enabled: bool) -> AgentConfig:
        if self._fleet is not None:
            try:
                agent = self._fleet.set_enabled(agent_id, enabled)
            except (FleetRegistryConfigurationError, FleetRegistryConflict) as exc:
                raise AgentInvalidConfigurationError(str(exc)) from exc
            if agent is None:
                raise AgentNotFoundError(agent_id)
            return agent
        agent = self.get(agent_id)
        if enabled and agent.token_file is None:
            raise AgentInvalidConfigurationError("enabled agents require a token_file")
        agent.enabled = enabled
        return agent

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
