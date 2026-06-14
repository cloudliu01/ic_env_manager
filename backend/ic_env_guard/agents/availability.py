import asyncio
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ic_env_guard.agents.client import AgentClientError, AgentHttpClient
from ic_env_guard.agents.models import API_VERSION, LOCAL_CAPABILITIES
from ic_env_guard.agents.registry import AgentRegistry


@dataclass(frozen=True)
class AgentObservation:
    status: str
    observed_at: datetime
    stale_after: datetime
    api_version: str | None = None
    agent_version: str | None = None
    capabilities: tuple[str, ...] = ()
    last_error: str | None = None


class AgentAvailabilityService:
    def __init__(
        self,
        registry: AgentRegistry,
        client: AgentHttpClient,
        stale_after_seconds: int = 30,
        max_parallel_probes: int = 8,
        probe_jitter_seconds: float = 1.0,
    ) -> None:
        self._registry = registry
        self._client = client
        self._stale_after_seconds = stale_after_seconds
        self._max_parallel_probes = max_parallel_probes
        self._probe_jitter_seconds = probe_jitter_seconds
        self._observations: dict[str, AgentObservation] = {}

    def summary(self, agent_id: str) -> dict[str, object]:
        summary = self._registry.summary(agent_id)
        agent = self._registry.get(agent_id)
        if not agent.enabled:
            return summary
        observation = self._observations.get(agent_id)
        if observation is None or observation.stale_after <= datetime.now(UTC):
            summary["status"] = "unknown"
            return summary
        summary.update(
            {
                "status": observation.status,
                "observed_at": observation.observed_at.isoformat(),
                "stale_after": observation.stale_after.isoformat(),
                "api_version": observation.api_version,
                "agent_version": observation.agent_version,
                "capabilities": list(observation.capabilities),
                "last_error": observation.last_error,
            }
        )
        return summary

    def list_summaries(self) -> list[dict[str, object]]:
        return [self.summary(agent.id) for agent in self._registry.list_configs()]

    def has_capability(self, agent_id: str, capability: str) -> bool:
        observation = self._observations.get(agent_id)
        if observation is None or observation.stale_after <= datetime.now(UTC):
            return False
        return capability in observation.capabilities

    async def ensure_capability(self, agent_id: str, capability: str) -> bool:
        observation = self._observations.get(agent_id)
        if observation is None or observation.stale_after <= datetime.now(UTC):
            await self.probe(agent_id)
        return self.has_capability(agent_id, capability)

    async def probe_all(self) -> None:
        semaphore = asyncio.Semaphore(self._max_parallel_probes)

        async def probe_with_limit(agent_id: str) -> None:
            if self._probe_jitter_seconds > 0:
                await asyncio.sleep(random.uniform(0, self._probe_jitter_seconds))
            async with semaphore:
                await self.probe(agent_id)

        await asyncio.gather(
            *(probe_with_limit(agent.id) for agent in self._registry.list_configs())
        )

    async def probe(self, agent_id: str) -> dict[str, object]:
        agent = self._registry.get(agent_id)
        if not agent.enabled:
            return self._registry.summary(agent_id)
        now = datetime.now(UTC)
        try:
            response = await self._client.request(agent, "GET", "/api/capabilities")
            payload = response.json()
            api_version = str(payload["api_version"])
            agent_version = str(payload["agent_version"])
            capabilities = tuple(str(capability) for capability in payload["capabilities"])
            missing_capabilities = sorted(set(LOCAL_CAPABILITIES) - set(capabilities))
            if api_version != API_VERSION:
                raise AgentClientError(
                    "agent_protocol_error", "unsupported agent api version"
                )
            observation = AgentObservation(
                status="degraded" if missing_capabilities else "ready",
                observed_at=now,
                stale_after=now + timedelta(seconds=self._stale_after_seconds),
                api_version=api_version,
                agent_version=agent_version,
                capabilities=capabilities,
                last_error="missing_capabilities" if missing_capabilities else None,
            )
        except (KeyError, TypeError, ValueError):
            observation = AgentObservation(
                status="unavailable",
                observed_at=now,
                stale_after=now + timedelta(seconds=self._stale_after_seconds),
                last_error="agent_protocol_error",
            )
        except AgentClientError as exc:
            observation = AgentObservation(
                status="unavailable",
                observed_at=now,
                stale_after=now + timedelta(seconds=self._stale_after_seconds),
                last_error=exc.category,
            )
        self._observations[agent_id] = observation
        return self.summary(agent_id)

    def record_ready_for_test(
        self,
        agent_id: str,
        observed_at: datetime,
        capabilities: tuple[str, ...] = ("services.v1",),
    ) -> None:
        self._observations[agent_id] = AgentObservation(
            status="ready",
            observed_at=observed_at,
            stale_after=observed_at + timedelta(seconds=self._stale_after_seconds),
            api_version="1",
            agent_version="0.2.0",
            capabilities=capabilities,
        )
